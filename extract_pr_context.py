#!/usr/bin/env python3
"""Extract complete changed C functions and their Doxygen comments from a PR."""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass
import json
import os
import re
import subprocess
import sys
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from tree_sitter import Language, Node, Parser
import tree_sitter_c


API_ROOT = "https://api.github.com"
HUNK_HEADER = re.compile(
    r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@"
)
C_EXTENSIONS = (".c", ".h")


class GitHubApiError(RuntimeError):
    """Raised when GitHub cannot provide the requested resource."""


@dataclass(frozen=True)
class FunctionContext:
    name: str
    start_line: int
    end_line: int
    doxygen: str | None
    source: str


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def get_json(
        self, endpoint: str, parameters: dict[str, str | int] | None = None
    ) -> Any:
        query = f"?{urlencode(parameters)}" if parameters else ""
        request = Request(
            f"{API_ROOT}{endpoint}{query}",
            headers=self._headers(),
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            message = f"GitHub returned HTTP {error.code} for {endpoint}: {detail}"
            if error.code == 404 and self._token is None:
                message += (
                    "\nHint: this repository may be private. Set GITHUB_TOKEN, "
                    "GITHUB_API_KEY, or run `gh auth login`."
                )
            raise GitHubApiError(message) from error
        except URLError as error:
            raise GitHubApiError(
                f"Could not connect to GitHub for {endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise GitHubApiError(
                f"GitHub returned invalid JSON for {endpoint}"
            ) from error

    def pull_request(self, repository: str, number: int) -> dict[str, Any]:
        result = self.get_json(f"/repos/{repository}/pulls/{number}")
        if not isinstance(result, dict):
            raise GitHubApiError("Unexpected pull request response")
        return result

    def pull_request_files(
        self, repository: str, number: int
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            result = self.get_json(
                f"/repos/{repository}/pulls/{number}/files",
                {"per_page": 100, "page": page},
            )
            if not isinstance(result, list):
                raise GitHubApiError("Unexpected pull request files response")
            files.extend(item for item in result if isinstance(item, dict))
            if len(result) < 100:
                return files
            page += 1

    def file_content(self, repository: str, path: str, ref: str) -> bytes:
        encoded_path = quote(path, safe="/")
        result = self.get_json(
            f"/repos/{repository}/contents/{encoded_path}",
            {"ref": ref},
        )
        if not isinstance(result, dict) or result.get("encoding") != "base64":
            raise GitHubApiError(f"Unexpected content response for {path}@{ref}")
        content = result.get("content")
        if not isinstance(content, str):
            raise GitHubApiError(f"Missing content for {path}@{ref}")
        try:
            return base64.b64decode(content, validate=False)
        except ValueError as error:
            raise GitHubApiError(f"Invalid base64 content for {path}@{ref}") from error

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "doxygen-diff-context-extractor",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers


def changed_lines(patch: str | None) -> tuple[set[int], set[int]]:
    old_changed: set[int] = set()
    new_changed: set[int] = set()
    old_line = 0
    new_line = 0

    for line in (patch or "").splitlines():
        header = HUNK_HEADER.match(line)
        if header:
            old_line = int(header.group("old"))
            new_line = int(header.group("new"))
        elif line.startswith("-"):
            old_changed.add(old_line)
            old_line += 1
        elif line.startswith("+"):
            new_changed.add(new_line)
            new_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1

    return old_changed, new_changed


def descendants(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from descendants(child)


def function_name(node: Node, source: bytes) -> str:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return "<anonymous>"

    identifiers = [
        child for child in descendants(declarator) if child.type == "identifier"
    ]
    if not identifiers:
        return source[declarator.start_byte : declarator.end_byte].decode(
            "utf-8", errors="replace"
        )
    identifier = identifiers[0]
    return source[identifier.start_byte : identifier.end_byte].decode(
        "utf-8", errors="replace"
    )


def preceding_doxygen(
    function: Node, comments: list[Node], source: bytes
) -> str | None:
    candidates = [
        comment for comment in comments if comment.end_byte <= function.start_byte
    ]
    if not candidates:
        return None

    comment = max(candidates, key=lambda item: item.end_byte)
    gap = source[comment.end_byte : function.start_byte]
    text = source[comment.start_byte : comment.end_byte]
    if gap.strip() or not (text.startswith(b"/**") or text.startswith(b"/*!")):
        return None
    return text.decode("utf-8", errors="replace")


def parse_functions(source: bytes) -> list[FunctionContext]:
    language = Language(tree_sitter_c.language())
    parser = Parser(language)
    tree = parser.parse(source)
    nodes = list(descendants(tree.root_node))
    comments = [node for node in nodes if node.type == "comment"]

    functions: list[FunctionContext] = []
    for node in nodes:
        if node.type != "function_definition":
            continue
        functions.append(
            FunctionContext(
                name=function_name(node, source),
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                doxygen=preceding_doxygen(node, comments, source),
                source=source[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="replace"
                ),
            )
        )
    return functions


def touches_lines(function: FunctionContext, lines: set[int]) -> bool:
    return any(function.start_line <= line <= function.end_line for line in lines)


def context_changed(
    base: FunctionContext | None, head: FunctionContext | None
) -> bool:
    if base is None or head is None:
        return True
    return base.source != head.source or base.doxygen != head.doxygen


def index_functions(
    functions: list[FunctionContext],
) -> dict[str, FunctionContext]:
    return {function.name: function for function in functions}


def changed_function_contexts(
    base_source: bytes | None,
    head_source: bytes | None,
    patch: str | None,
) -> list[dict[str, Any]]:
    base_functions = parse_functions(base_source) if base_source is not None else []
    head_functions = parse_functions(head_source) if head_source is not None else []
    base_by_name = index_functions(base_functions)
    head_by_name = index_functions(head_functions)
    old_lines, new_lines = changed_lines(patch)

    names = {
        name
        for name in base_by_name.keys() | head_by_name.keys()
        if context_changed(base_by_name.get(name), head_by_name.get(name))
        or (
            name in base_by_name
            and touches_lines(base_by_name[name], old_lines)
        )
        or (
            name in head_by_name
            and touches_lines(head_by_name[name], new_lines)
        )
    }

    return [
        {
            "name": name,
            "base": (
                asdict(base_by_name[name]) if name in base_by_name else None
            ),
            "head": (
                asdict(head_by_name[name]) if name in head_by_name else None
            ),
        }
        for name in sorted(names)
    ]


def extract_pull_request(
    client: GitHubClient, repository: str, number: int
) -> dict[str, Any]:
    pull_request = client.pull_request(repository, number)
    base_sha = pull_request["base"]["sha"]
    head_sha = pull_request["head"]["sha"]
    results: list[dict[str, Any]] = []

    for changed_file in client.pull_request_files(repository, number):
        path = changed_file.get("filename")
        if not isinstance(path, str) or not path.lower().endswith(C_EXTENSIONS):
            continue

        status = changed_file.get("status")
        base_path = changed_file.get("previous_filename", path)
        base_source = (
            None
            if status == "added"
            else client.file_content(repository, base_path, base_sha)
        )
        head_source = (
            None
            if status == "removed"
            else client.file_content(repository, path, head_sha)
        )
        functions = changed_function_contexts(
            base_source,
            head_source,
            changed_file.get("patch"),
        )
        if functions:
            results.append(
                {
                    "path": path,
                    "previous_path": (
                        base_path if base_path != path else None
                    ),
                    "status": status,
                    "functions": functions,
                }
            )

    return {
        "repository": repository,
        "pull_request": number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files": results,
    }


def format_function_section(label: str, context: dict[str, Any] | None) -> list[str]:
    if context is None:
        return [f"{label}: <missing>"]

    lines = [
        f"{label}: {context['name']} "
        f"(lines {context['start_line']}-{context['end_line']})"
    ]
    doxygen = context.get("doxygen")
    if doxygen:
        lines.append("Doxygen:")
        lines.extend(doxygen.splitlines())
    else:
        lines.append("Doxygen: <none>")

    source = context.get("source")
    if source:
        lines.append("Function:")
        lines.extend(source.splitlines())
    else:
        lines.append("Function: <missing>")

    return lines


def format_console_output(result: dict[str, Any]) -> str:
    lines = [
        f"Repository: {result['repository']}",
        f"Pull request: #{result['pull_request']}",
        f"Base: {result['base_sha']}",
        f"Head: {result['head_sha']}",
        "",
    ]

    files = result.get("files", [])
    if not files:
        lines.append("No changed C functions found.")
        return "\n".join(lines) + "\n"

    for changed_file in files:
        path = changed_file["path"]
        lines.append(f"=== {path} ===")
        if changed_file.get("previous_path"):
            lines.append(f"Previous path: {changed_file['previous_path']}")
        lines.append(f"Status: {changed_file.get('status', 'unknown')}")
        lines.append("")

        for function in changed_file.get("functions", []):
            lines.append(f"--- {function['name']} ---")
            lines.extend(format_function_section("Base", function.get("base")))
            lines.append("")
            lines.extend(format_function_section("Head", function.get("head")))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract complete changed C functions and adjacent Doxygen "
            "comments from a GitHub pull request."
        )
    )
    parser.add_argument("repository", help="GitHub repository in OWNER/REPO form")
    parser.add_argument("pull_request", type=int, help="Pull request number")
    parser.add_argument(
        "-o",
        "--output",
        help="Write JSON to this file instead of standard output",
    )
    return parser.parse_args()


def load_dotenv(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def gh_auth_token() -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def resolve_github_token() -> str | None:
    load_dotenv()
    for name in ("GITHUB_TOKEN", "GITHUB_API_KEY", "GH_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token
    return gh_auth_token()


def validate_arguments(repository: str, pull_request: int) -> None:
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name or "/" in name:
        raise ValueError("repository must use the OWNER/REPO format")
    if pull_request < 1:
        raise ValueError("pull request number must be positive")


def main() -> int:
    arguments = parse_arguments()
    try:
        validate_arguments(arguments.repository, arguments.pull_request)
        result = extract_pull_request(
            GitHubClient(resolve_github_token()),
            arguments.repository,
            arguments.pull_request,
        )
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if arguments.output:
            with open(arguments.output, "w", encoding="utf-8") as output:
                output.write(serialized)
        else:
            sys.stdout.write(format_console_output(result))
        return 0
    except (GitHubApiError, KeyError, TypeError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

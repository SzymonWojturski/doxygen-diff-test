#!/usr/bin/env python3
"""Build PRPart objects from a GitHub pull request with minimal REST API usage."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from tree_sitter import Language, Node, Parser
import tree_sitter_c

from pr_part import ObjType, PRAnalysisState, PRPart


API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
HUNK_HEADER = re.compile(
    r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@"
)
C_EXTENSIONS = (".c", ".h")
DOXYGEN_PREFIXES = (b"/**", b"/*!", b"///", b"//!")


class GitHubApiError(RuntimeError):
    """Raised when GitHub cannot provide the requested resource."""


@dataclass(frozen=True)
class ParsedObject:
    obj_type: ObjType
    start_line: int
    end_line: int
    code: str
    comments: str | None


class RestCounter:
    def __init__(self) -> None:
        self.count = 0


class GitHubPullRequestClient:
    """Uses REST API only for PR metadata and changed-file lists."""

    def __init__(self, token: str | None, counter: RestCounter) -> None:
        self._token = token
        self._counter = counter

    def pull_request(self, repository: str, number: int) -> dict[str, Any]:
        return self._get_json(f"/repos/{repository}/pulls/{number}")

    def pull_request_files(
        self, repository: str, number: int
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._get_json(
                f"/repos/{repository}/pulls/{number}/files",
                {"per_page": 100, "page": page},
            )
            if not isinstance(batch, list):
                raise GitHubApiError("Unexpected pull request files response")
            files.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                return files
            page += 1

    def _get_json(
        self, endpoint: str, parameters: dict[str, str | int] | None = None
    ) -> Any:
        self._counter.count += 1
        query = f"?{urlencode(parameters)}" if parameters else ""
        request = Request(
            f"{API_ROOT}{endpoint}{query}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "doxygen-pr-part-builder",
                "X-GitHub-Api-Version": "2022-11-28",
                **(
                    {"Authorization": f"Bearer {self._token}"}
                    if self._token
                    else {}
                ),
            },
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


class FileContentFetcher:
    """Loads file blobs without using the GitHub REST contents API."""

    def __init__(self, repository: str, token: str | None) -> None:
        self._repository = repository
        self._token = token
        self._cache: dict[tuple[str, str], bytes | None] = {}

    def fetch(self, sha: str, path: str) -> bytes | None:
        key = (sha, path)
        if key in self._cache:
            return self._cache[key]

        content = self._fetch_local_git(sha, path)
        if content is None:
            content = self._fetch_raw(sha, path)

        self._cache[key] = content
        return content

    def _fetch_local_git(self, sha: str, path: str) -> bytes | None:
        try:
            return subprocess.check_output(
                ["git", "show", f"{sha}:{path}"],
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    def _fetch_raw(self, sha: str, path: str) -> bytes | None:
        owner, name = self._repository.split("/", 1)
        url = f"{RAW_ROOT}/{owner}/{name}/{quote(sha)}/{quote(path)}"
        headers = {"User-Agent": "doxygen-pr-part-builder"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if error.code == 404:
                return None
            raise GitHubApiError(
                f"Could not fetch raw file {path}@{sha}: HTTP {error.code}"
            ) from error
        except URLError as error:
            raise GitHubApiError(
                f"Could not fetch raw file {path}@{sha}: {error.reason}"
            ) from error


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


def changed_new_lines(patch: str | None) -> set[int]:
    lines: set[int] = set()
    new_line = 0
    for line in (patch or "").splitlines():
        header = HUNK_HEADER.match(line)
        if header:
            new_line = int(header.group("new"))
        elif line.startswith("+"):
            lines.add(new_line)
            new_line += 1
        elif line.startswith(" "):
            new_line += 1
    return lines


def descendants(node: Node) -> Iterable[Node]:
    yield node
    for child in node.children:
        yield from descendants(child)


def node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def preceding_comment(node: Node, comments: list[Node], source: bytes) -> str | None:
    candidates = [
        comment for comment in comments if comment.end_byte <= node.start_byte
    ]
    if not candidates:
        return None

    comment = max(candidates, key=lambda item: item.end_byte)
    gap = source[comment.end_byte : node.start_byte]
    text = source[comment.start_byte : comment.end_byte]
    if gap.strip():
        return None
    if not any(text.startswith(prefix) for prefix in DOXYGEN_PREFIXES):
        return None
    return text.decode("utf-8", errors="replace")


def function_name(node: Node, source: bytes) -> str:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return "<anonymous>"
    identifiers = [
        child for child in descendants(declarator) if child.type == "identifier"
    ]
    if not identifiers:
        return node_text(declarator, source)
    return node_text(identifiers[0], source)


def declaration_obj_type(node: Node) -> ObjType:
    for child in descendants(node):
        if child.type == "function_declarator":
            return ObjType.FUNCTION_DECL
        if child.type == "struct_specifier":
            return ObjType.STRUCT
        if child.type == "enum_specifier":
            return ObjType.ENUM
        if child.type == "union_specifier":
            return ObjType.UNION
    return ObjType.VARIABLE


def object_span(
    index: int, nodes: list[Node], source: bytes
) -> tuple[int, int, str, int]:
    node = nodes[index]
    end_node = node
    next_index = index + 1
    if node.type in {"struct_specifier", "enum_specifier", "union_specifier"}:
        if next_index < len(nodes) and nodes[next_index].type == ";":
            end_node = nodes[next_index]
            next_index += 1
    code = source[node.start_byte : end_node.end_byte].decode("utf-8", errors="replace")
    return node.start_point[0] + 1, end_node.end_point[0] + 1, code, next_index


def extract_file_comment(source: bytes, comments: list[Node]) -> str | None:
    for comment in sorted(comments, key=lambda item: item.start_byte):
        text = source[comment.start_byte : comment.end_byte]
        if not (text.startswith(b"/**") or text.startswith(b"/*!")):
            continue
        decoded = text.decode("utf-8", errors="replace")
        if "@file" in decoded:
            return decoded
        return None
    return None


def classify_node(node: Node) -> ObjType | None:
    if node.type == "function_definition":
        return ObjType.FUNCTION
    if node.type == "preproc_def":
        return ObjType.MACRO
    if node.type == "type_definition":
        return ObjType.TYPEDEF
    if node.type == "struct_specifier":
        return ObjType.STRUCT
    if node.type == "enum_specifier":
        return ObjType.ENUM
    if node.type == "union_specifier":
        return ObjType.UNION
    if node.type == "declaration":
        return declaration_obj_type(node)
    return None


def parse_objects(source: bytes) -> tuple[list[ParsedObject], list[Node]]:
    parser = Parser(Language(tree_sitter_c.language()))
    tree = parser.parse(source)
    root = tree.root_node
    nodes = list(root.children)
    comments = [node for node in descendants(root) if node.type == "comment"]
    objects: list[ParsedObject] = []

    index = 0
    while index < len(nodes):
        node = nodes[index]
        obj_type = classify_node(node)
        if obj_type is None:
            index += 1
            continue

        start_line, end_line, code, index = object_span(index, nodes, source)
        objects.append(
            ParsedObject(
                obj_type=obj_type,
                start_line=start_line,
                end_line=end_line,
                code=code,
                comments=preceding_comment(node, comments, source),
            )
        )

    return objects, comments


def touches_lines(item: ParsedObject, lines: set[int]) -> bool:
    return any(item.start_line <= line <= item.end_line for line in lines)


def build_parts_for_file(
    path: str,
    source: bytes,
    patch: str | None,
    status: str | None,
) -> list[PRPart]:
    objects, comments = parse_objects(source)

    if status == "added":
        selected = objects
    elif not patch:
        selected = objects
    else:
        changed_lines = changed_new_lines(patch)
        if not changed_lines:
            selected = []
        else:
            selected = [obj for obj in objects if touches_lines(obj, changed_lines)]

    parts: list[PRPart] = []
    if status == "added":
        parts.append(
            PRPart(
                obj_type=ObjType.FILE,
                filepath=path,
                code=None,
                comments=extract_file_comment(source, comments),
            )
        )

    parts.extend(
        PRPart(
            obj_type=obj.obj_type,
            filepath=path,
            code=obj.code,
            comments=obj.comments,
        )
        for obj in selected
        if obj.obj_type != ObjType.FILE
    )
    return parts


def build_pr_parts(repository: str, pull_request: int) -> PRAnalysisState:
    token = resolve_github_token()
    counter = RestCounter()
    client = GitHubPullRequestClient(token, counter)
    fetcher = FileContentFetcher(repository, token)

    metadata = client.pull_request(repository, pull_request)
    head_sha = metadata["head"]["sha"]
    changed_files = client.pull_request_files(repository, pull_request)

    parts: list[PRPart] = []
    skipped: list[str] = []

    for changed in changed_files:
        path = changed.get("filename")
        if not isinstance(path, str) or not path.lower().endswith(C_EXTENSIONS):
            continue

        status = changed.get("status")
        if status == "removed":
            continue

        source = fetcher.fetch(head_sha, path)
        if source is None:
            skipped.append(path)
            continue

        patch = changed.get("patch")
        if patch is None and status != "added":
            print(
                f"warning: skipping {path}; GitHub omitted patch and file is not new",
                file=sys.stderr,
            )
            continue

        parts.extend(build_parts_for_file(path, source, patch, status))

    print(
        f"github rest calls: {counter.count} "
        f"(metadata + changed files; file contents fetched via git/raw)",
        file=sys.stderr,
    )
    if skipped:
        print(
            "warning: could not load files: " + ", ".join(skipped),
            file=sys.stderr,
        )

    return PRAnalysisState(parts=parts)


def format_console_output(state: PRAnalysisState) -> str:
    lines: list[str] = []
    if not state.parts:
        lines.append("No changed Doxygen objects found.")
        return "\n".join(lines) + "\n"

    current_filepath: str | None = None
    for part in state.parts:
        if part.filepath != current_filepath:
            current_filepath = part.filepath
            lines.append(f"=== {current_filepath} ===")
            lines.append("")

        lines.append(f"--- {part.obj_type.value} ---")
        if part.comments:
            lines.append("Comments:")
            lines.extend(part.comments.splitlines())
        else:
            lines.append("Comments: <none>")

        if part.code:
            lines.append("Code:")
            lines.extend(part.code.splitlines())
        elif part.obj_type != ObjType.FILE:
            lines.append("Code: <none>")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build PRPart objects from a GitHub pull request while minimizing "
            "GitHub REST API usage."
        )
    )
    parser.add_argument("repository", help="GitHub repository in OWNER/REPO form")
    parser.add_argument("pull_request", type=int, help="Pull request number")
    parser.add_argument(
        "-o",
        "--output",
        help="Write JSON to this file instead of stdout",
    )
    return parser.parse_args()


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
        state = build_pr_parts(arguments.repository, arguments.pull_request)
        if arguments.output:
            with open(arguments.output, "w", encoding="utf-8") as output:
                output.write(state.model_dump_json(indent=2) + "\n")
        else:
            sys.stdout.write(format_console_output(state))
        return 0
    except (GitHubApiError, KeyError, TypeError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

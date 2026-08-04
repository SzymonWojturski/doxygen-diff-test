"""Minimal LangGraph data model for Doxygen PR review."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ObjType(str, Enum):
    FILE = "file"
    FUNCTION = "function"
    FUNCTION_DECL = "function_decl"
    VARIABLE = "variable"
    ENUM = "enum"
    STRUCT = "struct"
    UNION = "union"
    TYPEDEF = "typedef"
    MACRO = "macro"


class PRPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    obj_type: ObjType
    filepath: str | None = None
    code: str | None = None
    comments: str | None = None


class PRAnalysisState(BaseModel):
    parts: list[PRPart] = Field(default_factory=list)

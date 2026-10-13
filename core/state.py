"""Shared state passed between LangGraph nodes.

Agents never call each other. Each reads the fields it needs from this state and
returns a partial update plus one message for the trace.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class MigrationState(TypedDict, total=False):
    # inputs
    source_schema: dict
    target_schema: dict
    mapping: list[dict]
    sample_rows: dict
    # memory agent
    memory_hints: list[dict]
    # schema analyst
    facts: dict
    brief: dict
    # sql generator
    plan: dict
    statements: list[dict]
    sql_text: str
    attempt: int
    # validator
    issues: list[dict]
    feedback: list[dict]
    passed: bool
    dry_run: dict
    history: Annotated[list[dict], operator.add]
    # explainer
    explanation: dict
    # audit trail
    messages: Annotated[list[dict], operator.add]
    config: dict[str, Any]

"""Input and database safety boundaries for Jet Center's Ask TDG feature."""

import os
import re
from dataclasses import dataclass
from typing import Any

import psycopg2

MAX_QUESTION_CHARS = 2_000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_TEXT_CHARS = 2_000
MAX_QUERY_ROWS = 20
STATEMENT_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def validate_ask_payload(body: Any) -> tuple[str, list[dict[str, str]]]:
    """Validate and bound the untrusted browser payload before building prompts."""
    if not isinstance(body, dict):
        raise ValueError("Ask TDG requires a valid JSON object.")

    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("No question provided.")
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise ValueError("Question must be 2,000 characters or fewer.")

    history = body.get("history") or []
    if not isinstance(history, list):
        raise ValueError("History must be a list.")
    if len(history) > MAX_HISTORY_MESSAGES:
        raise ValueError("History must contain no more than 12 messages.")

    validated_history = []
    for message in history:
        if not isinstance(message, dict):
            raise ValueError("History contains an invalid message.")
        role = message.get("role")
        text = message.get("text")
        if role not in {"user", "assistant"} or not isinstance(text, str):
            raise ValueError("History contains an invalid message.")
        text = text.strip()
        if len(text) > MAX_HISTORY_TEXT_CHARS:
            raise ValueError("Each history message must be 2,000 characters or fewer.")
        validated_history.append({"role": role, "text": text})

    return question, validated_history


def _single_select(sql: str) -> str:
    """Accept one plain SELECT statement; reject comments, CTE writes, and stacking."""
    if not isinstance(sql, str):
        raise ValueError("Ask TDG generated query must be a single SELECT statement.")
    candidate = sql.strip()
    if candidate.endswith(";"):
        candidate = candidate[:-1].rstrip()
    if not re.match(r"(?is)^SELECT\b", candidate) or ";" in candidate:
        raise ValueError("Ask TDG generated query must be a single SELECT statement.")
    return candidate


def execute_read_only_query(sql: str) -> QueryResult:
    """Execute a bounded SELECT in an enforced read-only PostgreSQL transaction."""
    candidate = _single_select(sql)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Jet Center database is not configured")

    connection = None
    cursor = None
    try:
        connection = psycopg2.connect(database_url, connect_timeout=10)
        connection.set_session(readonly=True, autocommit=False)
        cursor = connection.cursor()
        cursor.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
        cursor.execute(candidate)
        fetched = cursor.fetchmany(MAX_QUERY_ROWS + 1)
        if cursor.description is None:
            raise RuntimeError("Ask TDG query returned no result columns")
        columns = [description[0] for description in cursor.description]
        return QueryResult(
            columns=columns,
            rows=fetched[:MAX_QUERY_ROWS],
            truncated=len(fetched) > MAX_QUERY_ROWS,
        )
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()

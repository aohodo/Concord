"""Small dependency-free full-text index for non-authoritative retrieval candidates."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

_LATIN_TOKEN = re.compile(r"[a-z0-9_]{2,}", re.IGNORECASE)
_CJK_SPAN = re.compile(r"[\u3400-\u9fff]+")
_SAFE_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")


def _search_terms(text: str) -> str:
    """Create explicit Latin tokens and CJK uni/bi-grams for SQLite FTS5."""

    lowered = text.casefold()
    terms = _LATIN_TOKEN.findall(lowered)
    for span in _CJK_SPAN.findall(lowered):
        terms.extend(span)
        terms.extend(span[index : index + 2] for index in range(len(span) - 1))
    return " ".join(dict.fromkeys(terms))


class SQLiteTextIndex:
    """Persistent lexical retrieval with namespace and scope isolation."""

    def __init__(self, root: str | Path, namespace: str) -> None:
        if not _SAFE_NAMESPACE.fullmatch(namespace):
            raise ValueError("invalid SQLite text-index namespace")
        root_path = Path(root)
        root_path.mkdir(parents=True, exist_ok=True)
        self._database = root_path / "retrieval.sqlite3"
        self._table = f"{namespace}_fts"
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {self._table} USING fts5(
                    doc_id UNINDEXED,
                    scope UNINDEXED,
                    title UNINDEXED,
                    metadata_json UNINDEXED,
                    content UNINDEXED,
                    terms
                )
                """
            )

    def upsert(
        self,
        *,
        doc_id: str,
        scope: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        terms = _search_terms(f"{title}\n{content}")
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM {self._table} WHERE doc_id = ?",
                (str(doc_id),),
            )
            connection.execute(
                f"""
                INSERT INTO {self._table}
                    (doc_id, scope, title, metadata_json, content, terms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(doc_id),
                    str(scope),
                    str(title),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    str(content),
                    terms,
                ),
            )

    def search(self, query: str, *, scope: str, limit: int) -> list[dict[str, Any]]:
        terms = _search_terms(query).split()
        if not terms:
            return []
        match = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:32])
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT doc_id, title, metadata_json, content, bm25({self._table}) AS rank
                FROM {self._table}
                WHERE {self._table} MATCH ? AND scope = ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, str(scope), max(1, min(int(limit), 50))),
            ).fetchall()
        return [
            {
                "doc_id": row["doc_id"],
                "title": row["title"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
                "content": row["content"],
                "rank": float(row["rank"]),
            }
            for row in rows
        ]

    def count(self, *, scope: str | None = None) -> int:
        with self._connect() as connection:
            if scope is None:
                row = connection.execute(
                    f"SELECT COUNT(*) AS count FROM {self._table}"
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT COUNT(*) AS count FROM {self._table} WHERE scope = ?",
                    (str(scope),),
                ).fetchone()
        return int(row["count"] if row is not None else 0)


__all__ = ["SQLiteTextIndex"]

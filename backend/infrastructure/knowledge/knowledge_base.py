"""Local full-text index for curated, non-authoritative knowledge chunks."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from infrastructure.retrieval.sqlite_text_index import SQLiteTextIndex


class KnowledgeBase:
    CHUNK_SIZE = 900
    CHUNK_OVERLAP = 120

    def __init__(self, index_path: str = "./data/retrieval") -> None:
        self._index = SQLiteTextIndex(index_path, "curated_knowledge")

    async def add_documents_async(self, documents: list[dict[str, Any]]) -> int:
        chunks: list[tuple[str, str, str, dict[str, Any]]] = []
        for document in documents:
            title = str(document.get("title") or "untitled")
            content = str(document.get("content") or "").strip()
            metadata = dict(document.get("metadata") or {})
            for chunk_number, text in enumerate(self._split(content)):
                digest = hashlib.sha256(
                    f"{title}\0{chunk_number}\0{text}".encode()
                ).hexdigest()
                chunks.append(
                    (
                        digest,
                        title,
                        text,
                        {
                            **self._scalar_metadata(metadata),
                            "chunk": chunk_number,
                            "authority": "curated_reference_not_case_fact",
                        },
                    )
                )
        for doc_id, title, content, metadata in chunks:
            await asyncio.to_thread(
                self._index.upsert,
                doc_id=doc_id,
                scope="curated",
                title=title,
                content=content,
                metadata=metadata,
            )
        return len(chunks)

    async def search_async(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not query.strip() or await self.doc_count_async() == 0:
            return []
        matches = await asyncio.to_thread(
            self._index.search,
            query,
            scope="curated",
            limit=max(1, min(top_k, 20)),
        )
        return [
            {
                "title": item["title"] or "untitled",
                "chunk": item["metadata"].get("chunk", index),
                "content": item["content"],
                "score": round(1.0 / (index + 1), 6),
                "authority": "retrieval_candidate_only",
            }
            for index, item in enumerate(matches)
        ]

    async def doc_count_async(self) -> int:
        return int(await asyncio.to_thread(self._index.count, scope="curated"))

    @classmethod
    def _split(cls, content: str) -> list[str]:
        if not content:
            return []
        step = cls.CHUNK_SIZE - cls.CHUNK_OVERLAP
        return [
            content[start : start + cls.CHUNK_SIZE]
            for start in range(0, len(content), step)
            if content[start : start + cls.CHUNK_SIZE].strip()
        ]

    @staticmethod
    def _scalar_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
        return {
            str(key): value
            for key, value in metadata.items()
            if isinstance(value, (str, int, float, bool))
        }

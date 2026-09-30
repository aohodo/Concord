import asyncio

from infrastructure.knowledge.knowledge_base import KnowledgeBase
from infrastructure.retrieval.sqlite_text_index import SQLiteTextIndex


def test_sqlite_text_index_keeps_user_scopes_isolated(tmp_path):
    index = SQLiteTextIndex(tmp_path, "recall_test")
    index.upsert(
        doc_id="one",
        scope="user-a",
        title="VPN incident",
        content="错误码 691，修改域密码后无法连接。",
    )
    index.upsert(
        doc_id="two",
        scope="user-b",
        title="VPN incident",
        content="错误码 691，但这是另一个用户的记录。",
    )

    matches = index.search("VPN 错误码 691", scope="user-a", limit=5)

    assert [item["doc_id"] for item in matches] == ["one"]
    assert index.count(scope="user-a") == 1


def test_curated_knowledge_returns_non_authoritative_candidates(tmp_path):
    knowledge = KnowledgeBase(index_path=str(tmp_path))

    added = asyncio.run(
        knowledge.add_documents_async(
            [
                {
                    "title": "VPN runbook",
                    "content": "错误码 691 通常需要检查保存的认证凭据。",
                    "metadata": {"source": "test"},
                }
            ]
        )
    )
    results = asyncio.run(knowledge.search_async("VPN 691 认证", top_k=3))

    assert added == 1
    assert results[0]["title"] == "VPN runbook"
    assert results[0]["authority"] == "retrieval_candidate_only"

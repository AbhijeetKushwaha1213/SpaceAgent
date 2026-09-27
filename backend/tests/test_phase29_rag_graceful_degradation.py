"""SENTINEL — Phase 1J RAG graceful-degradation regression (test_phase29_...)

Root cause of the Phase-4 audit RAG failures: ``initialize_pdf_rag`` read the
persisted ChromaDB index with a bare ``collection.count()``. Every other
ChromaDB call in that function is wrapped to fall back to the always-available
FALLBACK_KB, but this one was not — so a corrupt or version-incompatible index
(which raises ``InternalError`` at ``count()``) escaped all the way up and
hard-failed the RAG stage, defeating the module's own guarantee that a ChromaDB
error degrades to the fallback KB and retrieval "never returns an empty list".

This test forces ``count()`` to raise and pins the contract:
  * ``initialize_pdf_rag`` returns False (degraded) instead of raising,
  * PDF-RAG availability is marked False with the error recorded,
  * ``retrieve_procedures_traced`` still returns attributable snippets from the
    fallback KB — the exact shape the audit trail asserts.

It controls all state via monkeypatch, so it is independent of whether a real
ChromaDB index is present on the machine running it.
"""

from __future__ import annotations

from app.agent import rag


class _BoomCollection:
    """A ChromaDB collection whose read fails like a corrupt persisted index."""

    def count(self):
        raise RuntimeError(
            "simulated chromadb.errors.InternalError: metadata segment reader: "
            "mismatched types; Rust type `u64` is not compatible with `BLOB`"
        )


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def list_collections(self):
        return []

    def delete_collection(self, *args, **kwargs):
        pass

    def get_or_create_collection(self, *args, **kwargs):
        return _BoomCollection()


def _install_broken_chroma(monkeypatch):
    import chromadb

    monkeypatch.setattr(chromadb, "PersistentClient", _FakeClient)
    # Non-None embedding fn so init proceeds past the embedding gate to count().
    monkeypatch.setattr(rag, "_get_embedding_fn", lambda: object())
    # Reset the module singleton so init actually re-runs (auto-restored).
    monkeypatch.setattr(rag._rag_status, "initialized", False)
    monkeypatch.setattr(rag._rag_status, "available", False)
    monkeypatch.setattr(rag._rag_status, "last_error", None)
    monkeypatch.setattr(rag, "_chroma_collection", None)


def test_count_error_degrades_instead_of_raising(monkeypatch):
    _install_broken_chroma(monkeypatch)

    # Must NOT raise — the whole point of the fix.
    result = rag.initialize_pdf_rag(force_rebuild=False)

    assert result is False
    assert rag._rag_status.available is False
    assert rag._rag_status.last_error and "count failed" in rag._rag_status.last_error


def test_traced_retrieval_falls_back_to_kb_on_count_error(monkeypatch):
    _install_broken_chroma(monkeypatch)

    snippets, trace = rag.retrieve_procedures_traced(
        query="safe mode recovery",
        fault_cues=["GYRO_A_RATE", "SEU_COUNTER"],
        top_k=3,
        use_pdf_rag=True,
    )

    # Retrieval still produced attributable procedure context via the fallback.
    assert len(snippets) > 0
    assert trace["backend"] == "fallback_kb"
    assert trace["snippet_count"] == len(snippets)
    assert "query" in trace
    assert trace["sources"], "fallback retrieval must record its sources"
    first = trace["sources"][0]
    for field in ("source_kind", "identifier", "content_sha256"):
        assert field in first
    assert first["source_kind"] == "fallback_kb"

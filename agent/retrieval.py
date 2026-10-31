"""Elasticsearch retrieval helpers for the conversational RAG feature."""

from __future__ import annotations

from typing import Any

import requests


class RetrievalError(RuntimeError):
    """Raised when historical-document retrieval cannot complete."""


def search_historical_documents(
    *, base_url: str, index_name: str, timeout: float, query: str, size: int = 6
) -> list[dict[str, Any]]:
    """Run a keyword retrieval against campaign content and file metadata.

    The query is intentionally lexical so it works with the existing index. It
    can be replaced by Elasticsearch hybrid/vector retrieval later without a UI
    or agent-contract change.
    """
    normalized_query = query.strip()
    if not normalized_query:
        return []
    body = {
        "size": size,
        "_source": [
            "campaign_id",
            "document_type",
            "source_files",
            "document_files",
            "extracted_text",
            "created_at",
            "status",
        ],
        "query": {
            "multi_match": {
                "query": normalized_query,
                "fields": ["extracted_text^3", "source_files^2", "document_files.file_name^2"],
                "type": "best_fields",
            }
        },
        "sort": ["_score", {"created_at": {"order": "desc", "unmapped_type": "date"}}],
    }
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/{index_name}/_search", json=body, timeout=timeout
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RetrievalError(f"Elasticsearch search failed: {exc}") from exc
    return response.json().get("hits", {}).get("hits", [])


def build_grounding_context(hits: list[dict[str, Any]], max_characters: int = 10_000) -> str:
    """Create numbered, bounded evidence for an LLM prompt."""
    sections: list[str] = []
    remaining = max_characters
    for position, hit in enumerate(hits, start=1):
        source = hit.get("_source", {})
        names = source.get("source_files") or [item.get("file_name", "Unknown file") for item in source.get("document_files", [])]
        excerpt = str(source.get("extracted_text") or "").strip()
        if not excerpt:
            continue
        section = (
            f"[Source {position}]\n"
            f"Files: {', '.join(names) or 'Pasted campaign brief'}\n"
            f"Uploaded: {source.get('created_at', 'Unknown')}\n"
            f"Content:\n{excerpt}\n"
        )
        if len(section) > remaining:
            section = section[:remaining]
        sections.append(section)
        remaining -= len(section)
        if remaining <= 0:
            break
    return "\n\n".join(sections)


def referenced_files(hit: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize current and legacy indexed-file metadata for the UI."""
    source = hit.get("_source", {})
    archived = source.get("document_files") or []
    if archived:
        return archived
    return [{"file_name": name, "legacy_record": True} for name in source.get("source_files") or []]


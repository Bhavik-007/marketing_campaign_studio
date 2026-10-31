"""Safe local archival and retrieval of original campaign uploads."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


class HistoricalDocumentError(RuntimeError):
    """Raised when archived-file metadata is unsafe or cannot be read."""


def archive_uploaded_files(uploaded_files: Iterable[Any], storage_root: Path) -> list[dict[str, Any]]:
    """Persist uploads once and return JSON-serializable metadata for Elasticsearch."""
    root = storage_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for uploaded_file in uploaded_files:
        original_name = Path(uploaded_file.name).name
        if not original_name:
            raise HistoricalDocumentError("An uploaded file did not have a valid filename.")
        document_id = str(uuid4())
        relative_path = Path(document_id) / original_name
        destination = (root / relative_path).resolve()
        if root not in destination.parents:
            raise HistoricalDocumentError("The uploaded filename resolved outside the document archive.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = uploaded_file.getvalue()
        destination.write_bytes(content)
        records.append(
            {
                "document_id": document_id,
                "file_name": original_name,
                "file_type": Path(original_name).suffix.lower().lstrip(".") or "unknown",
                "mime_type": getattr(uploaded_file, "type", None) or "application/octet-stream",
                "storage_path": relative_path.as_posix(),
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return records


def read_archived_file(document: dict[str, Any], storage_root: Path) -> tuple[bytes, str, str]:
    """Load an archived file after validating its metadata-derived path."""
    storage_path = str(document.get("storage_path") or "")
    file_name = Path(str(document.get("file_name") or "")).name
    if not storage_path or not file_name:
        raise HistoricalDocumentError("This historical record does not include downloadable file metadata.")

    root = storage_root.resolve()
    candidate = (root / storage_path).resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise HistoricalDocumentError("The archived file is unavailable at its recorded storage location.")
    return candidate.read_bytes(), file_name, str(document.get("mime_type") or "application/octet-stream")


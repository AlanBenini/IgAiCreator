import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from backend.config import EXPORTS_DIR
from backend.models import ContentDraft, CopyItem


CONTENT_FILE = EXPORTS_DIR / "content_library.json"


def _read() -> list[dict]:
    if not CONTENT_FILE.exists():
        return []
    try:
        return json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write(rows: list[dict]) -> None:
    CONTENT_FILE.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")


def create_draft(title: str, content_format: str, asset_ids: list[str], copies: list[CopyItem]) -> ContentDraft:
    draft = ContentDraft(id=uuid.uuid4().hex, title=title, content_format=content_format, status="DRAFT", asset_ids=asset_ids, copies=copies, created_at=datetime.now(timezone.utc).isoformat())
    rows = _read()
    rows.insert(0, draft.model_dump())
    _write(rows)
    return draft


def list_drafts() -> list[ContentDraft]:
    return [ContentDraft(**row) for row in _read()]


def update_status(content_id: str, status: str) -> ContentDraft:
    if status not in {"DRAFT", "APPROVED", "PUBLISHED"}:
        raise HTTPException(status_code=400, detail="Stato contenuto non valido.")
    rows = _read()
    for row in rows:
        if row["id"] == content_id:
            row["status"] = status
            _write(rows)
            return ContentDraft(**row)
    raise HTTPException(status_code=404, detail="Contenuto non trovato.")
"""Coda delle pubblicazioni programmate.

Instagram non espone una vera pianificazione: un contenuto si pubblica solo
chiamando `media_publish`, e il container creato prima scade dopo 24 ore. Per
questo teniamo una coda locale e la processiamo quando l'orario è passato:

- il contenitore viene creato **al momento della pubblicazione**, non quando
  l'utente programma il post, così non scade mai prima dell'uso;
- il processore è idempotente: una voce pubblicata non viene più toccata;
- chi chiama il processore può iniettare un publisher (serve ai test e alla
  modalità demo, che non deve toccare la Graph API).

`schaduled_at` è un istante UTC in ISO: il frontend converte l'orario scelto
dall'utente nel suo fuso, così il confronto con `now` è sempre coerente anche
quando il server gira in un fuso diverso.
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from backend.adapters.instagram import publish_photo
from backend.config import EXPORTS_DIR, USE_MOCK
from backend.models import PublicationRunResult, ScheduledPost
from backend.services.assets import asset_public_url


PUBLICATIONS_FILE = EXPORTS_DIR / "publications.json"
STATUSES = {"SCHEDULED", "PUBLISHED", "FAILED", "CANCELLED"}


def _read() -> list[dict]:
    if not PUBLICATIONS_FILE.exists():
        return []
    try:
        return json.loads(PUBLICATIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write(rows: list[dict]) -> None:
    PUBLICATIONS_FILE.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")


def _parse_moment(value: str) -> datetime:
    """Istante UTC. Accetta ISO con fuso (Z o +hh:mm) e, per tolleranza, senza."""
    try:
        moment = datetime.fromisoformat((value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Data di pubblicazione non valida: {value}.") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def create_publication(caption: str, asset_ids: list[str], scheduled_at: str, user_id: str | None = None, calendar_event_id: str | None = None, calendar_event_title: str | None = None) -> ScheduledPost:
    if not asset_ids:
        raise HTTPException(status_code=400, detail="Carica almeno un'immagine da pubblicare.")
    moment = _parse_moment(scheduled_at)
    entry = ScheduledPost(
        id=uuid.uuid4().hex,
        user_id=user_id,
        caption=(caption or "").strip(),
        asset_ids=list(asset_ids),
        scheduled_at=moment.astimezone(timezone.utc).isoformat(),
        status="SCHEDULED",
        calendar_event_id=calendar_event_id or None,
        calendar_event_title=calendar_event_title or None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    rows = _read()
    rows.insert(0, entry.model_dump())
    _write(rows)
    return entry


def list_publications(user_id: str | None = None) -> list[ScheduledPost]:
    rows = _read()
    if user_id:
        # In locale (senza login) le voci hanno user_id nullo: restano visibili.
        rows = [row for row in rows if row.get("user_id") in (None, user_id)]
    return [ScheduledPost(**row) for row in rows]


def cancel_publication(publication_id: str) -> ScheduledPost:
    rows = _read()
    for row in rows:
        if row["id"] != publication_id:
            continue
        if row["status"] == "PUBLISHED":
            raise HTTPException(status_code=409, detail="Il contenuto è già stato pubblicato: non si può annullare.")
        row["status"] = "CANCELLED"
        _write(rows)
        return ScheduledPost(**row)
    raise HTTPException(status_code=404, detail="Pubblicazione programmata non trovata.")


def due_publications(now: datetime | None = None) -> list[ScheduledPost]:
    moment = now or datetime.now(timezone.utc)
    return [entry for entry in list_publications() if entry.status == "SCHEDULED" and _parse_moment(entry.scheduled_at) <= moment]


def _default_publisher(entry: ScheduledPost) -> str:
    if USE_MOCK:
        # Modalità demo: nessuna chiamata a Meta, ma la coda si comporta davvero.
        return f"mock_{entry.id[:8]}"
    if not entry.asset_ids:
        raise RuntimeError("La pubblicazione non ha immagini associate.")
    return publish_photo(asset_public_url(entry.asset_ids[0]), entry.caption, user_id=entry.user_id)


def process_due(now: datetime | None = None, publisher=None) -> PublicationRunResult:
    """Pubblica le voci scadute. Una voce per volta: un errore non blocca le altre."""
    moment = now or datetime.now(timezone.utc)
    publish = publisher or _default_publisher
    rows = _read()
    results = []
    published = 0
    failed = 0
    due = 0

    for row in rows:
        if row["status"] != "SCHEDULED" or _parse_moment(row["scheduled_at"]) > moment:
            continue
        due += 1
        entry = ScheduledPost(**row)
        try:
            row["platform_id"] = publish(entry)
            row["status"] = "PUBLISHED"
            row["published_at"] = datetime.now(timezone.utc).isoformat()
            row["error"] = None
            published += 1
        except Exception as error:  # noqa: BLE001 - qualunque errore diventa uno stato visibile
            row["status"] = "FAILED"
            row["error"] = str(error)[:300]
            failed += 1
        results.append(ScheduledPost(**row))

    if due:
        _write(rows)
    return PublicationRunResult(due=due, published=published, failed=failed, results=results)
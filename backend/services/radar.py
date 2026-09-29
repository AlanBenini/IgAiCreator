from datetime import datetime, timedelta, timezone

from backend.adapters.google_calendar import list_events
from backend.models import Opportunity


# Brief di raccolta condiviso: lo usano sia il Content Radar sia il piano
# editoriale, così le due funzioni suggeriscono lo stesso materiale.
CAPTURE_BRIEF = ["Foto panoramica dell'evento", "Dettaglio del prodotto o dell'attivita", "Foto del team o dei partecipanti", "Video verticale di 10-15 secondi"]


def build_opportunities(user_id: str | None) -> list[Opportunity]:
    if not user_id:
        return []
    now = datetime.now(timezone.utc)
    events = list_events(user_id, now.isoformat(), (now + timedelta(days=30)).isoformat())
    opportunities = []
    for event in events:
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "")
        title = event.get("summary", "Evento aziendale")
        days = (datetime.fromisoformat(start.replace("Z", "+00:00")) - now).days if "T" in start else 30
        priority = "ALTA" if days <= 2 else "MEDIA" if days <= 7 else "BASSA"
        opportunities.append(Opportunity(id=event["id"], title=title, start=start, priority=priority, reason="Questo evento puo diventare un contenuto per il brand.", capture_brief=list(CAPTURE_BRIEF)))
    return opportunities
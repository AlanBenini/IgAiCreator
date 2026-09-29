"""Piano editoriale: post ricorrenti e riunioni legate all'argomento del post.

Il tempo vive su Google Calendar (fonte di verità per il calendario), mentre qui
teniamo il piano che lo ha generato: così resta elencabile, verificabile e
rimovibile in blocco. Un piano crea due serie ricorrenti:

- `Post · <argomento>` nei giorni di pubblicazione scelti;
- `Riunione · <argomento>` nei giorni delle riunioni, spostati di N giorni
  rispetto al post quando serve fare il punto prima di pubblicare.

Lo stesso modulo rileva le riunioni che si ripetono nello stesso giorno della
settimana in settimane diverse: è il segnale che l'utente sta già lavorando a
una cadenza, quindi vale la pena proporre un piano editoriale.
"""

import json
import statistics
import uuid
from datetime import date, datetime, timedelta, timezone

import requests
from fastapi import HTTPException

from backend.adapters.google_calendar import create_recurring_event, delete_event
from backend.config import EXPORTS_DIR
from backend.models import EditorialPlan, EditorialPlanRequest, MeetingSuggestion
from backend.services.radar import CAPTURE_BRIEF


PLANS_FILE = EXPORTS_DIR / "editorial_plans.json"
WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
WEEKDAY_LABELS = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]
TIME_FORMAT = "%H:%M"
FORMAT_LABELS = {
    "instagram_post": "Instagram post",
    "instagram_carousel": "Instagram carousel",
    "instagram_reel": "Instagram Reel",
    "instagram_story": "Instagram Stories",
    "linkedin_post": "LinkedIn post",
}
# Quante ore possono separare due riunioni per considerarli la stessa abitudine
# (09:00 e 10:00 sono lo stesso appuntamento spostato).
MERGE_WINDOW_HOURS = 1
MINIMUM_OCCURRENCES = 3


def _read() -> list[dict]:
    if not PLANS_FILE.exists():
        return []
    try:
        return json.loads(PLANS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write(rows: list[dict]) -> None:
    PLANS_FILE.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")


def _parse_time(value: str) -> str:
    try:
        parsed = datetime.strptime((value or "").strip(), TIME_FORMAT)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Orario non valido: {value}. Usa il formato HH:MM.") from None
    return parsed.strftime(TIME_FORMAT)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Data non valida: {value}. Usa il formato AAAA-MM-GG.") from None


def _validate_days(days: list[str], field: str) -> list[str]:
    cleaned = [(day or "").strip().upper() for day in days if (day or "").strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"Seleziona almeno un giorno per {field}.")
    unknown = [day for day in cleaned if day not in WEEKDAY_CODES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Giorni non riconosciuti per {field}: {', '.join(unknown)}.")
    # Ordine settimanale e niente duplicati.
    return [code for code in WEEKDAY_CODES if code in cleaned]


def shift_weekdays(days: list[str], shift: int) -> list[str]:
    """Sposta i giorni di N posizioni nella settimana (0 = stesso giorno)."""
    if not shift:
        return list(days)
    indexes = {code: index for index, code in enumerate(WEEKDAY_CODES)}
    return [WEEKDAY_CODES[index] for index in sorted({(indexes[day] + shift) % 7 for day in days})]


def _series_start(monday: date, weekday: str, shift_days: int, time_value: str) -> str:
    """DTSTART di una serie ricorrente.

    Per i post lo spostamento è 0: la serie parte dal giorno scelto nella
    settimana indicata. Per le riunioni lo DTSTART è il giorno del post spostato
    di N giorni: ancorare lì la serie è ciò che fa combaciare ogni riunione con
    il proprio post, anche quando lo spostamento cade nella settimana
    precedente (una riunione il giorno prima di un post del lunedì).
    """
    anchor = monday + timedelta(days=WEEKDAY_CODES.index(weekday) + shift_days)
    return datetime.combine(anchor, datetime.strptime(time_value, TIME_FORMAT).time()).strftime("%Y-%m-%dT%H:%M:%S")


def _add_minutes(start: str, minutes: int) -> str:
    return (datetime.fromisoformat(start) + timedelta(minutes=max(5, minutes))).strftime("%Y-%m-%dT%H:%M:%S")


def _post_description(payload: EditorialPlanRequest, topic: str) -> str:
    lines = [
        f"Argomento: {topic}",
        f"Formato: {FORMAT_LABELS.get(payload.content_format, payload.content_format)}",
        f"Cadenza: {len(payload.post_days)} post a settimana per {payload.weeks} settimane",
    ]
    if payload.notes.strip():
        lines.append(f"Note: {payload.notes.strip()}")
    lines.append("")
    lines.append("Materiale da raccogliere:")
    lines.extend(f"- {item}" for item in CAPTURE_BRIEF)
    lines.append("")
    lines.append("Dal calendario di MOMENT puoi caricare le foto e programmare la pubblicazione di questo giorno.")
    return "\n".join(lines)


def _meeting_description(payload: EditorialPlanRequest, topic: str) -> str:
    lines = [
        f"Riunione legata al post: {topic}",
        "Ogni riunione prepara il contenuto del giorno di pubblicazione.",
    ]
    if payload.notes.strip():
        lines.append(f"Note: {payload.notes.strip()}")
    lines.append("")
    lines.append("Ordine del giorno suggerito:")
    lines.extend(f"- {item}" for item in CAPTURE_BRIEF)
    return "\n".join(lines)


def create_plan(payload: EditorialPlanRequest, user_id: str) -> EditorialPlan:
    topic = (payload.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Indica l'argomento del piano editoriale.")
    if payload.frequency not in {"weekly", "biweekly"}:
        raise HTTPException(status_code=400, detail="Frequenza non supportata: usa weekly o biweekly.")
    if payload.weeks < 1 or payload.weeks > 52:
        raise HTTPException(status_code=400, detail="Scegli da 1 a 52 settimane.")
    if abs(payload.meeting_shift_days) > 6:
        raise HTTPException(status_code=400, detail="Lo spostamento della riunione va da -6 a +6 giorni.")

    # La serie parte sempre dal lunedì della settimana scelta: così i giorni
    # selezionati cadono nella settimana giusta anche se l'utente indica un
    # mercoledì come data di inizio.
    start_monday = _parse_date(payload.start_date)
    start_monday -= timedelta(days=start_monday.weekday())
    post_days = _validate_days(payload.post_days, "i post")
    meeting_days = shift_weekdays(post_days, payload.meeting_shift_days)
    post_time = _parse_time(payload.post_time)
    meeting_time = _parse_time(payload.meeting_time)

    post_start = _series_start(start_monday, post_days[0], 0, post_time)
    meeting_start = _series_start(start_monday, post_days[0], payload.meeting_shift_days, meeting_time)

    post_event = create_recurring_event(
        user_id,
        f"Post · {topic}",
        _post_description(payload, topic),
        post_start,
        _add_minutes(post_start, payload.post_duration_minutes),
        payload.frequency,
        post_days,
        payload.weeks,
    )
    meeting_event = create_recurring_event(
        user_id,
        f"Riunione · {topic}",
        _meeting_description(payload, topic),
        meeting_start,
        _add_minutes(meeting_start, payload.meeting_duration_minutes),
        payload.frequency,
        meeting_days,
        payload.weeks,
    )

    plan = EditorialPlan(
        id=uuid.uuid4().hex,
        topic=topic,
        content_format=payload.content_format,
        start_date=start_monday.isoformat(),
        weeks=payload.weeks,
        frequency=payload.frequency,
        post_days=post_days,
        post_time=post_time,
        post_duration_minutes=max(5, payload.post_duration_minutes),
        meeting_days=meeting_days,
        meeting_time=meeting_time,
        meeting_duration_minutes=max(5, payload.meeting_duration_minutes),
        notes=payload.notes.strip(),
        posts_planned=len(post_days) * payload.weeks,
        meetings_planned=len(meeting_days) * payload.weeks,
        post_event_id=post_event.get("id"),
        post_event_link=post_event.get("htmlLink"),
        meeting_event_id=meeting_event.get("id"),
        meeting_event_link=meeting_event.get("htmlLink"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    rows = _read()
    rows.insert(0, plan.model_dump())
    _write(rows)
    return plan


def list_plans() -> list[EditorialPlan]:
    return [EditorialPlan(**row) for row in _read()]


def delete_plan(plan_id: str, user_id: str) -> None:
    """Rimuove il piano e le due serie su Google Calendar.

    Il record locale viene rimosso comunque: se Google rifiuta l'eliminazione di
    una serie l'errore risale al chiamante, per non lasciare l'utente convinto
    che il calendario sia pulito.
    """
    rows = _read()
    plan = next((row for row in rows if row["id"] == plan_id), None)
    if not plan:
        raise HTTPException(status_code=404, detail="Piano editoriale non trovato.")
    errors = []
    for key in ("post_event_id", "meeting_event_id"):
        event_id = plan.get(key)
        if not event_id:
            continue
        try:
            delete_event(user_id, event_id)
        except (RuntimeError, requests.RequestException) as error:
            errors.append(str(error))
    _write([row for row in rows if row["id"] != plan_id])
    if errors:
        raise RuntimeError("Piano rimosso, ma Google Calendar ha segnalato: " + " | ".join(errors))


def _bucket_events(events: list[dict]) -> dict[str, dict[str, dict]]:
    """Raggruppa per giorno della settimana e settimana, contando una volta al giorno."""
    buckets: dict[str, dict[str, dict]] = {}
    for event in events:
        start = (event.get("start") or {}).get("dateTime")
        if not start:
            # Gli eventi di giornata intera non hanno un orario da confrontare.
            continue
        try:
            moment = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        weekday = WEEKDAY_CODES[moment.weekday()]
        monday = (moment.date() - timedelta(days=moment.weekday())).isoformat()
        buckets.setdefault(weekday, {}).setdefault(monday, event)
    return buckets


def _merge_adjacent(candidates: list[dict], minimum_occurrences: int) -> list[MeetingSuggestion]:
    """Unisce le fasce orarie contigue dello stesso giorno (09:00 e 10:00)."""
    by_weekday: dict[str, list[dict]] = {}
    for item in candidates:
        by_weekday.setdefault(item["weekday"], []).append(item)

    suggestions = []
    for weekday, items in by_weekday.items():
        items.sort(key=lambda item: item["hour"])
        groups: list[dict] = []
        for item in items:
            group = groups[-1] if groups else None
            if group and item["hour"] - group["hour"] <= MERGE_WINDOW_HOURS:
                group["weeks"].update(item["weeks"])
                group["titles"] = list(dict.fromkeys(group["titles"] + item["titles"]))
                continue
            groups.append({"weekday": weekday, "hour": item["hour"], "weeks": set(item["weeks"]), "titles": list(item["titles"])})
        for group in groups:
            if len(group["weeks"]) < minimum_occurrences:
                continue
            label = WEEKDAY_LABELS[WEEKDAY_CODES.index(weekday)]
            occurrences = len(group["weeks"])
            suggestions.append(MeetingSuggestion(
                weekday=weekday,
                weekday_label=label,
                hour=group["hour"],
                occurrences=occurrences,
                weeks=sorted(group["weeks"]),
                titles=group["titles"][:3],
                message=f"{label} alle {group['hour']:02d}:00 hai {occurrences} riunioni in {occurrences} settimane diverse: vuoi trasformarle in un piano editoriale?",
            ))
    suggestions.sort(key=lambda item: (-item.occurrences, item.weekday, item.hour))
    return suggestions


def detect_recurring_meetings(events: list[dict], minimum_occurrences: int = MINIMUM_OCCURRENCES) -> list[MeetingSuggestion]:
    """Riunioni nello stesso giorno della settimana in almeno N settimane diverse.

    La soglia si applica dopo l'unione delle fasce contigue: due lunedì alle
    09:00 più un lunedì alle 10:00 sono la stessa abitudine e vanno contati
    insieme, altrimenti non raggiungerebbero mai la soglia.
    """
    candidates = []
    for weekday, weeks in _bucket_events(events).items():
        hours = []
        for event in weeks.values():
            start = (event.get("start") or {}).get("dateTime", "")
            try:
                hours.append(datetime.fromisoformat(start.replace("Z", "+00:00")).hour)
            except ValueError:
                continue
        if not hours:
            continue
        candidates.append({
            "weekday": weekday,
            "hour": round(statistics.median(hours)),
            "weeks": set(weeks.keys()),
            "titles": [event.get("summary", "Riunione") for event in weeks.values()],
        })
    return _merge_adjacent(candidates, minimum_occurrences)

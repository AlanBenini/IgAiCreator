import time
from urllib.parse import urlencode

import requests

from backend.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


SCOPES = "openid email profile https://www.googleapis.com/auth/calendar.events"
OAUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def authorization_url(state: str) -> str:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Configura GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nelle variabili d'ambiente di Vercel.")
    params = {"client_id": GOOGLE_CLIENT_ID, "redirect_uri": GOOGLE_REDIRECT_URI, "response_type": "code", "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state}
    return f"{OAUTH_URL}?{urlencode(params)}"


def _save_token(user_id: str, token: dict) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Configura Supabase per salvare il collegamento Google Calendar.")
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/google_connections",
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"},
        json={"user_id": user_id, "access_token": token["access_token"], "refresh_token": token.get("refresh_token", ""), "expires_at": token["expires_at"]},
        timeout=20,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError("Impossibile salvare il collegamento Google Calendar su Supabase.")


def exchange_code(code: str, user_id: str) -> None:
    response = requests.post(TOKEN_URL, data={"code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "redirect_uri": GOOGLE_REDIRECT_URI, "grant_type": "authorization_code"}, timeout=30)
    response.raise_for_status()
    token = response.json()
    token["expires_at"] = int(time.time()) + token.get("expires_in", 3600)
    _save_token(user_id, token)


def save_provider_token(user_id: str, access_token: str, refresh_token: str = "") -> None:
    _save_token(user_id, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + 3600,
    })


def has_connection(user_id: str) -> bool:
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/google_connections",
        params={"user_id": f"eq.{user_id}", "select": "user_id"},
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
        timeout=20,
    )
    response.raise_for_status()
    return bool(response.json())


def _access_token(user_id: str) -> str:
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/google_connections",
        params={"user_id": f"eq.{user_id}", "select": "access_token,refresh_token,expires_at"},
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise RuntimeError("Collega prima Google Calendar")
    token = rows[0]
    if token.get("expires_at", 0) <= time.time() + 60 and token.get("refresh_token"):
        response = requests.post(TOKEN_URL, data={"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "refresh_token": token["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
        response.raise_for_status()
        token.update(response.json())
        token["expires_at"] = int(time.time()) + token.get("expires_in", 3600)
        _save_token(user_id, token)
    return token["access_token"]


def create_event(user_id: str, title: str, description: str, start: str, end: str) -> dict:
    response = requests.post(CALENDAR_URL, headers={"Authorization": f"Bearer {_access_token(user_id)}", "Content-Type": "application/json"}, json={"summary": title, "description": description, "start": {"dateTime": start}, "end": {"dateTime": end}}, timeout=30)
    response.raise_for_status()
    return response.json()


def create_recurring_event(user_id: str, title: str, description: str, start: str, end: str, frequency: str, days: list[str], weeks: int) -> dict:
    if frequency not in {"weekly", "biweekly"}:
        raise RuntimeError("Frequenza non supportata.")
    if not days or any(day not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"} for day in days) or weeks < 1 or weeks > 52:
        raise RuntimeError("Seleziona almeno un giorno e da 1 a 52 settimane.")
    interval = 2 if frequency == "biweekly" else 1
    recurrence = f"RRULE:FREQ=WEEKLY;INTERVAL={interval};BYDAY={','.join(days)};COUNT={len(days) * weeks}"
    body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start, "timeZone": "Europe/Rome"},
        "end": {"dateTime": end, "timeZone": "Europe/Rome"},
        "recurrence": [recurrence],
    }
    response = requests.post(CALENDAR_URL, headers={"Authorization": f"Bearer {_access_token(user_id)}", "Content-Type": "application/json"}, json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def delete_event(user_id: str, event_id: str) -> None:
    response = requests.delete(
        f"{CALENDAR_URL}/{event_id}",
        headers={"Authorization": f"Bearer {_access_token(user_id)}"},
        timeout=30,
    )
    if response.status_code not in {200, 204}:
        detail = response.json().get("error", {}).get("message", response.text[:300])
        raise RuntimeError(f"Google Calendar non ha potuto eliminare l'evento: {detail}")


def delete_events(user_id: str, event_ids: list[str]) -> int:
    if not event_ids or len(event_ids) > 100:
        raise RuntimeError("Seleziona da 1 a 100 eventi da eliminare.")
    for event_id in event_ids:
        delete_event(user_id, event_id)
    return len(event_ids)


def list_events(user_id: str, time_min: str, time_max: str) -> list[dict]:
    response = requests.get(CALENDAR_URL, headers={"Authorization": f"Bearer {_access_token(user_id)}"}, params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime"}, timeout=30)
    if response.status_code >= 400:
        detail = response.json().get("error", {}).get("message", response.text[:300])
        raise RuntimeError(f"Google Calendar ha rifiutato la richiesta: {detail}")
    return response.json().get("items", [])
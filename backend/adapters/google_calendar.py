import json
import secrets
import time
from urllib.parse import urlencode

import requests

from backend.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GOOGLE_TOKEN_DIR


SCOPES = "openid email profile https://www.googleapis.com/auth/calendar.events"
OAUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def authorization_url() -> str:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Configura GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET nel file .env")
    params = {"client_id": GOOGLE_CLIENT_ID, "redirect_uri": GOOGLE_REDIRECT_URI, "response_type": "code", "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": secrets.token_urlsafe(16)}
    return f"{OAUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> str:
    response = requests.post(TOKEN_URL, data={"code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "redirect_uri": GOOGLE_REDIRECT_URI, "grant_type": "authorization_code"}, timeout=30)
    response.raise_for_status()
    token = response.json()
    token["expires_at"] = int(time.time()) + token.get("expires_in", 3600)
    user_response = requests.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=30)
    user_response.raise_for_status()
    user_id = user_response.json()["sub"]
    (GOOGLE_TOKEN_DIR / f"{user_id}.json").write_text(json.dumps(token), encoding="utf-8")
    return user_id


def _access_token(user_id: str) -> str:
    token_file = GOOGLE_TOKEN_DIR / f"{user_id}.json"
    if not token_file.exists():
        raise RuntimeError("Collega prima Google Calendar")
    token = json.loads(token_file.read_text(encoding="utf-8"))
    if token.get("expires_at", 0) <= time.time() + 60 and token.get("refresh_token"):
        response = requests.post(TOKEN_URL, data={"client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "refresh_token": token["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
        response.raise_for_status()
        token.update(response.json())
        token["expires_at"] = int(time.time()) + token.get("expires_in", 3600)
        token_file.write_text(json.dumps(token), encoding="utf-8")
    return token["access_token"]


def create_event(user_id: str, title: str, description: str, start: str, end: str) -> dict:
    response = requests.post(CALENDAR_URL, headers={"Authorization": f"Bearer {_access_token(user_id)}", "Content-Type": "application/json"}, json={"summary": title, "description": description, "start": {"dateTime": start}, "end": {"dateTime": end}}, timeout=30)
    response.raise_for_status()
    return response.json()


def list_events(user_id: str, time_min: str, time_max: str) -> list[dict]:
    response = requests.get(CALENDAR_URL, headers={"Authorization": f"Bearer {_access_token(user_id)}"}, params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime"}, timeout=30)
    response.raise_for_status()
    return response.json().get("items", [])
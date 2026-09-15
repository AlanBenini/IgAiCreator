import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse

from backend.config import BASE_DIR, STORAGE_DIR
from backend.models import AnalyticsSummary, AssetRecord, CalendarEventRequest, CalendarEventResponse, ContentDraft, ContentPublishResponse, ContentStatusRequest, CopyGenerationResponse, GoogleTokenRequest, Opportunity
from backend.services.assets import asset_public_url, list_assets, save_uploads
from backend.services.analytics import calculate_analytics
from backend.services.copywriter import generate_copies
from backend.services.content import create_draft, list_drafts, update_status
from backend.services.radar import build_opportunities
from backend.adapters.instagram import publish_photo
from backend.config import META_APP_ID, META_APP_SECRET, META_REDIRECT_URI, SESSION_SECRET, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, USE_MOCK
from backend.adapters.google_calendar import create_event, save_provider_token

app = FastAPI(title="Instagram Content Engine API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", include_in_schema=False)
def health_check():
    return {"status": "ok"}


@app.get("/api", include_in_schema=False)
def api_health_check():
    return JSONResponse({"status": "ok", "service": "moment-api"})


@app.get("/api/config", include_in_schema=False)
def frontend_config():
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
        "meta_app_id": META_APP_ID,
        "meta_redirect_uri": META_REDIRECT_URI,
    }


def _encode_state(user_id: str) -> str:
    if not SESSION_SECRET:
        raise HTTPException(status_code=503, detail="SESSION_SECRET non configurato.")
    payload = json.dumps({"user_id": user_id, "exp": int(time.time()) + 600}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _decode_state(state: str) -> str:
    try:
        body, signature = state.split(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padding = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{body}{padding}"))
        if payload["exp"] < time.time():
            raise ValueError
        return payload["user_id"]
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Stato OAuth non valido o scaduto.") from None


def _supabase_user(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer ") or not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise HTTPException(status_code=401, detail="Accedi prima con Google.")
    response = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": authorization},
        timeout=15,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Sessione non valida.")
    return response.json()["id"]


def _resolve_meta_instagram_id(page: dict, page_access_token: str) -> str:
    nested = page.get("instagram_business_account") or {}
    instagram_id = nested.get("id")
    if instagram_id:
        return instagram_id
    page_id = page.get("id")
    if not page_id or not page_access_token:
        return ""
    try:
        response = requests.get(
            f"https://graph.facebook.com/v21.0/{page_id}",
            params={"fields": "instagram_business_account", "access_token": page_access_token},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""
    payload = response.json().get("instagram_business_account") or {}
    return payload.get("id", "")


def _meta_pages_for_token(token: str) -> list[dict]:
    accounts = requests.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={"fields": "id,name,access_token,tasks,instagram_business_account", "access_token": token},
        timeout=30,
    )
    if accounts.status_code != 200:
        detail = accounts.json().get("error", {}).get("message", accounts.text[:300])
        raise HTTPException(status_code=502, detail=f"Meta non ha restituito le pagine: {detail}")
    pages = accounts.json().get("data", [])
    if pages:
        return pages

    # Pages assigned through Business Manager may not appear in /me/accounts.
    businesses = requests.get(
        "https://graph.facebook.com/v21.0/me/businesses",
        params={"fields": "id,name", "access_token": token},
        timeout=30,
    )
    if businesses.status_code != 200:
        return []
    for business in businesses.json().get("data", []):
        owned_pages = requests.get(
            f"https://graph.facebook.com/v21.0/{business['id']}/owned_pages",
            params={"fields": "id,name,access_token,tasks,instagram_business_account", "access_token": token},
            timeout=30,
        )
        if owned_pages.status_code == 200 and owned_pages.json().get("data"):
            return owned_pages.json()["data"]
        client_pages = requests.get(
            f"https://graph.facebook.com/v21.0/{business['id']}/client_pages",
            params={"fields": "id,name,access_token,tasks,instagram_business_account", "access_token": token},
            timeout=30,
        )
        if client_pages.status_code == 200 and client_pages.json().get("data"):
            return client_pages.json()["data"]
    return []


def _save_meta_connection(user_id: str, page: dict, instagram_id: str) -> None:
    if not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=503, detail="Configura SUPABASE_SERVICE_ROLE_KEY su Vercel.")
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/meta_connections",
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"},
        json={"user_id": user_id, "page_id": page["id"], "page_name": page.get("name", ""), "page_access_token": page["access_token"], "instagram_user_id": instagram_id or ""},
        timeout=20,
    )
    if response.status_code not in {200, 201}:
        raise HTTPException(status_code=502, detail="Impossibile salvare il collegamento Meta su Supabase.")


@app.get("/api/auth/meta/start")
def meta_auth_start(request: Request):
    user_id = _supabase_user(request)
    if not META_APP_ID or not META_APP_SECRET:
        raise HTTPException(status_code=503, detail="Configura META_APP_ID e META_APP_SECRET su Vercel.")
    params = {
        "client_id": META_APP_ID,
        "redirect_uri": META_REDIRECT_URI,
        "state": _encode_state(user_id),
        "response_type": "code",
        "auth_type": "rerequest",
        "return_scopes": "true",
        "scope": "pages_show_list,pages_read_engagement,business_management",
    }
    return {"url": f"https://www.facebook.com/v21.0/dialog/oauth?{urlencode(params)}"}


@app.get("/api/auth/meta/callback")
def meta_auth_callback(code: str = Query(...), state: str = Query(...)):
    user_id = _decode_state(state)
    token_response = requests.get("https://graph.facebook.com/v21.0/oauth/access_token", params={"client_id": META_APP_ID, "client_secret": META_APP_SECRET, "redirect_uri": META_REDIRECT_URI, "code": code}, timeout=30)
    token_response.raise_for_status()
    token = token_response.json()["access_token"]
    permissions = requests.get(
        "https://graph.facebook.com/v21.0/me/permissions",
        params={"access_token": token},
        timeout=30,
    )
    pages = _meta_pages_for_token(token)
    if not pages:
        me = requests.get("https://graph.facebook.com/v21.0/me", params={"fields": "id,name", "access_token": token}, timeout=30)
        me_detail = me.json().get("error", {}).get("message", me.text[:300]) if me.status_code != 200 else "Meta non associa pagine gestibili a questo token."
        permission_rows = permissions.json().get("data", []) if permissions.status_code == 200 else []
        granted = [item.get("permission") for item in permission_rows if item.get("status") == "granted"]
        declined = [item.get("permission") for item in permission_rows if item.get("status") != "granted"]
        raise HTTPException(status_code=400, detail=f"Meta non restituisce la pagina per l'account {me.json().get('name', 'selezionato')}: {me_detail} Permission concesse: {', '.join(granted) or 'nessuna'}. Permission rifiutate o mancanti: {', '.join(declined) or 'non indicate'}. Controlla Accesso alla Pagina per Bing Bong, poi riprova e accetta tutte le richieste Meta.")
    page = next((item for item in pages if item.get("name")), pages[0])
    instagram_id = _resolve_meta_instagram_id(page, page.get("access_token", token))
    if not instagram_id:
        # Alcune pagine Facebook sono collegate ma non esponono subito l'account Instagram nella risposta di /me/accounts.
        # Salviamo comunque la pagina per non bloccare il collegamento e permettere all'utente di tornare a usarla.
        _save_meta_connection(user_id, page, "")
        return RedirectResponse(url="/?meta=connected&page_only=1", status_code=303)
    _save_meta_connection(user_id, page, instagram_id)
    return RedirectResponse(url="/?meta=connected", status_code=303)


@app.get("/api/analytics", response_model=AnalyticsSummary)
def get_analytics(request: Request):
    user_id = _supabase_user(request) if request.headers.get("authorization") else None
    return calculate_analytics(user_id=user_id)


@app.post("/api/generate-copy", response_model=CopyGenerationResponse)
async def generate_copy(files: list[UploadFile] = File(...), theme: str | None = Form(default=None), content_format: str = Form(default="instagram_post"), title: str = Form(default="Nuovo contenuto")):
    assets = await save_uploads(files)
    try:
        copies = generate_copies(assets, theme, content_format)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    draft = create_draft(title, content_format, [asset.id for asset in assets], copies)
    return CopyGenerationResponse(assets=assets, copies=copies, draft=draft)


@app.get("/api/assets", response_model=list[AssetRecord])
def get_assets():
    return list_assets()


@app.get("/api/opportunities", response_model=list[Opportunity])
def get_opportunities(request: Request):
    user_id = _supabase_user(request) if request.headers.get("authorization") else None
    try:
        return build_opportunities(user_id)
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Impossibile leggere il calendario: {error}") from error


@app.get("/api/content", response_model=list[ContentDraft])
def get_content():
    return list_drafts()


@app.patch("/api/content/{content_id}", response_model=ContentDraft)
def approve_content(content_id: str, request: ContentStatusRequest):
    return update_status(content_id, request.status)


@app.post("/api/content/{content_id}/publish", response_model=ContentPublishResponse)
def publish_content(content_id: str, request: Request):
    drafts = list_drafts()
    draft = next((item for item in drafts if item.id == content_id), None)
    if not draft:
        raise HTTPException(status_code=404, detail="Contenuto non trovato.")
    if draft.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Approva il contenuto prima di pubblicarlo.")
    caption = draft.copies[0].caption if draft.copies else ""
    user_id = _supabase_user(request) if request.headers.get("authorization") else None
    if USE_MOCK:
        update_status(content_id, "PUBLISHED")
        return ContentPublishResponse(id=content_id, status="PUBLISHED", platform_id=f"mock_{content_id[:8]}")
    try:
        platform_id = publish_photo(asset_public_url(draft.asset_ids[0]), caption, user_id=user_id)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    update_status(content_id, "PUBLISHED")
    return ContentPublishResponse(id=content_id, status="PUBLISHED", platform_id=platform_id)


@app.post("/api/calendar/connect")
def calendar_connect(token: GoogleTokenRequest, request: Request):
    user_id = _supabase_user(request)
    try:
        save_provider_token(user_id, token.access_token, token.refresh_token)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"status": "connected"}


@app.post("/api/calendar/events", response_model=CalendarEventResponse)
def calendar_event(event: CalendarEventRequest, request: Request):
    user_id = _supabase_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Accedi prima con Google Calendar.")
    try:
        created = create_event(user_id, event.title, event.description, event.start, event.end)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return CalendarEventResponse(id=created["id"], html_link=created.get("htmlLink"))


# Statici e media sono serviti dallo stesso processo locale.
app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")
app.mount("/", StaticFiles(directory=BASE_DIR / "frontend", html=True), name="frontend")

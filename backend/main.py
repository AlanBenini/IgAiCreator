import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse

from backend.config import BASE_DIR, CORS_ORIGINS, STORAGE_DIR
from backend.models import AnalyticsSummary, AssetRecord, CalendarBulkDeleteRequest, CalendarEventRequest, CalendarEventResponse, CalendarScheduleRequest, CalendarUpdateRequest, ContentDraft, ContentPublishResponse, ContentStatusRequest, CopyGenerationResponse, EditorialPlan, EditorialPlanRequest, GoogleTokenRequest, MeetingSuggestion, Opportunity, PublicationRunResult, ScheduledPost
from backend.services.assets import asset_public_url, list_assets, save_uploads
from backend.services.analytics import calculate_analytics
from backend.services.copywriter import generate_copies
from backend.services.content import create_draft, list_drafts, update_status
from backend.services.editorial import create_plan, delete_plan, detect_recurring_meetings, list_plans
from backend.services.publications import cancel_publication, create_publication, list_publications, process_due
from backend.services.radar import build_opportunities
from backend.adapters.instagram import publish_photo
from backend.config import CRON_SECRET, META_APP_ID, META_APP_SECRET, META_REDIRECT_URI, SESSION_SECRET, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, USE_MOCK
from backend.adapters.google_calendar import create_event, create_recurring_event, delete_event, delete_events, has_connection, list_events, save_provider_token, update_event

app = FastAPI(title="Instagram Content Engine API", version="2.0.0")

# Il frontend è servito dallo stesso dominio del backend: nessuna origine esterna
# è autorizzata per default. Valorizza CORS_ORIGINS per domini diversi.
ALLOW_ANY_ORIGIN = CORS_ORIGINS == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    # Wildcard insieme alle credenziali non è valido: questa API usa Bearer token, non cookie.
    allow_credentials=bool(CORS_ORIGINS) and not ALLOW_ANY_ORIGIN,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def revalidate_static_files(request: Request, call_next):
    # Senza Cache-Control i browser applicano la freshness euristica e possono
    # riusare HTML/CSS/JS vecchi anche per ore dopo un aggiornamento. Qui si
    # impone la rivalidazione: il file resta in cache ma viene confermato con 304.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


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
        # Su Vercel lo storage è /tmp: il frontend lo dice all'utente prima che
        # programmi un post che non sopravvivrebbe al riavvio dell'istanza.
        "storage_ephemeral": bool(os.getenv("VERCEL")),
        "scheduler_configured": bool(CRON_SECRET),
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


# Quando Supabase non è configurato (sviluppo locale) il workspace resta usabile
# senza login; quando lo è, ogni endpoint protetto esige un token valido.
AUTH_REQUIRED = bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def require_user(request: Request) -> str | None:
    """Utente Supabase corrente, oppure None se il login non è configurato."""
    if not AUTH_REQUIRED:
        return None
    return _supabase_user(request)


def require_login(request: Request) -> str:
    user_id = require_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Accedi prima con Google.")
    return user_id


def require_calendar_user(request: Request) -> str:
    user_id = require_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Accedi prima con Google Calendar.")
    return user_id


if USE_MOCK:
    print("[MOMENT] USE_MOCK attivo: analytics e pubblicazione usano i post demo.")
if os.getenv("VERCEL"):
    print("[MOMENT] Storage serverless (/tmp) effimero: upload, bozze e media non sopravvivono tra le invocazioni.")
if not AUTH_REQUIRED:
    print("[MOMENT] Supabase non configurato: gli endpoint protetti accettano chiamate anonime (solo sviluppo locale).")


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
    user_id = require_login(request)
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
    return calculate_analytics(user_id=require_user(request))


@app.post("/api/generate-copy", response_model=CopyGenerationResponse)
async def generate_copy(request: Request, files: list[UploadFile] = File(...), theme: str | None = Form(default=None), content_format: str = Form(default="instagram_post"), title: str = Form(default="Nuovo contenuto")):
    require_user(request)
    assets = await save_uploads(files)
    try:
        copies = generate_copies(assets, theme, content_format)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    draft = create_draft(title, content_format, [asset.id for asset in assets], copies)
    return CopyGenerationResponse(assets=assets, copies=copies, draft=draft)


@app.get("/api/assets", response_model=list[AssetRecord])
def get_assets(request: Request):
    require_user(request)
    return list_assets()


@app.get("/api/opportunities", response_model=list[Opportunity])
def get_opportunities(request: Request):
    user_id = require_user(request)
    try:
        return build_opportunities(user_id)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile leggere il calendario: {error}") from error


@app.get("/api/calendar/events")
def get_calendar_events(request: Request, start: str = Query(...), end: str = Query(...)):
    user_id = require_calendar_user(request)
    try:
        return list_events(user_id, start, end)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile leggere il calendario: {error}") from error


@app.delete("/api/calendar/events/{event_id}")
def remove_calendar_event(event_id: str, request: Request):
    user_id = require_calendar_user(request)
    try:
        delete_event(user_id, event_id)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile eliminare l'evento: {error}") from error
    return {"status": "deleted"}


@app.post("/api/calendar/events/bulk-delete")
def remove_calendar_events(payload: CalendarBulkDeleteRequest, request: Request):
    user_id = require_calendar_user(request)
    try:
        deleted = delete_events(user_id, payload.event_ids)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile eliminare gli eventi: {error}") from error
    return {"status": "deleted", "count": deleted}


@app.patch("/api/calendar/events/{event_id}")
def edit_calendar_event(event_id: str, payload: CalendarUpdateRequest, request: Request):
    user_id = require_calendar_user(request)
    try:
        updated = update_event(user_id, event_id, payload.title, payload.description, payload.start, payload.end)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile modificare l'evento: {error}") from error
    return updated


@app.get("/api/content", response_model=list[ContentDraft])
def get_content(request: Request):
    require_user(request)
    return list_drafts()


@app.patch("/api/content/{content_id}", response_model=ContentDraft)
def approve_content(content_id: str, request: Request, payload: ContentStatusRequest):
    require_user(request)
    return update_status(content_id, payload.status)


@app.post("/api/content/{content_id}/publish", response_model=ContentPublishResponse)
def publish_content(content_id: str, request: Request):
    drafts = list_drafts()
    draft = next((item for item in drafts if item.id == content_id), None)
    if not draft:
        raise HTTPException(status_code=404, detail="Contenuto non trovato.")
    if draft.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Approva il contenuto prima di pubblicarlo.")
    caption = draft.copies[0].caption if draft.copies else ""
    user_id = require_user(request)
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
    user_id = require_login(request)
    try:
        save_provider_token(user_id, token.access_token, token.refresh_token)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"status": "connected"}


@app.get("/api/calendar/status")
def calendar_status(request: Request):
    user_id = require_login(request)
    try:
        return {"connected": has_connection(user_id)}
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Impossibile verificare Google Calendar: {error}") from error


@app.post("/api/calendar/events", response_model=CalendarEventResponse)
def calendar_event(event: CalendarEventRequest, request: Request):
    user_id = require_calendar_user(request)
    try:
        created = create_event(user_id, event.title, event.description, event.start, event.end)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return CalendarEventResponse(id=created["id"], html_link=created.get("htmlLink"))


@app.post("/api/calendar/events/recurring", response_model=CalendarEventResponse)
def calendar_recurring_event(event: CalendarScheduleRequest, request: Request):
    user_id = require_calendar_user(request)
    try:
        created = create_recurring_event(user_id, event.title, event.description, event.start, event.end, event.frequency, event.days, event.weeks)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile pianificare gli eventi: {error}") from error
    return CalendarEventResponse(id=created["id"], html_link=created.get("htmlLink"))


# ------------------------------------------------- piano editoriale e coda post


@app.get("/api/editorial/suggestions", response_model=list[MeetingSuggestion])
def editorial_suggestions(request: Request):
    """Riunioni ripetute nello stesso giorno in settimane diverse.

    È il segnale che l'utente sta già lavorando a una cadenza: il frontend lo
    mostra come promemoria e propone di convertirlo in un piano editoriale.
    """
    user_id = require_user(request)
    if not user_id:
        return []
    now = datetime.now(timezone.utc)
    try:
        events = list_events(user_id, now.isoformat(), (now + timedelta(days=45)).isoformat())
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile leggere il calendario: {error}") from error
    return detect_recurring_meetings(events)


@app.get("/api/editorial/plans", response_model=list[EditorialPlan])
def get_editorial_plans(request: Request):
    require_user(request)
    return list_plans()


@app.post("/api/editorial/plans", response_model=EditorialPlan)
def create_editorial_plan(payload: EditorialPlanRequest, request: Request):
    user_id = require_calendar_user(request)
    try:
        return create_plan(payload, user_id)
    except HTTPException:
        raise
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=f"Impossibile creare il piano editoriale: {error}") from error


@app.delete("/api/editorial/plans/{plan_id}")
def remove_editorial_plan(plan_id: str, request: Request):
    user_id = require_calendar_user(request)
    try:
        delete_plan(plan_id, user_id)
    except HTTPException:
        raise
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"status": "deleted"}


def _require_scheduler(request: Request) -> None:
    """Il processore pubblica davvero: non resta mai aperto a chiunque.

    - con CRON_SECRET configurato serve il Bearer condiviso con lo scheduler;
    - senza CRON_SECRET in produzione l'endpoint è chiuso (503) invece di
      esporre una pubblicazione libera;
    - in sviluppo locale, senza Supabase, non c'è nulla da proteggere.
    """
    if CRON_SECRET:
        if request.headers.get("authorization", "") == f"Bearer {CRON_SECRET}":
            return
        raise HTTPException(status_code=403, detail="Scheduler non autorizzato.")
    if AUTH_REQUIRED:
        raise HTTPException(status_code=503, detail="Configura CRON_SECRET per abilitare il processore delle pubblicazioni.")
    return


@app.get("/api/publications", response_model=list[ScheduledPost])
def get_publications(request: Request):
    return list_publications(require_user(request))


@app.post("/api/publications", response_model=ScheduledPost)
async def schedule_publication(
    request: Request,
    files: list[UploadFile] | None = File(default=None),
    asset_ids: str = Form(default=""),
    caption: str = Form(default=""),
    scheduled_at: str = Form(...),
    calendar_event_id: str = Form(default=""),
    calendar_event_title: str = Form(default=""),
):
    """Programma un post: si caricano foto nuove oppure si riusano asset già presenti."""
    user_id = require_user(request)
    chosen = [item.strip() for item in asset_ids.split(",") if item.strip()]
    if files:
        chosen.extend(asset.id for asset in await save_uploads(files))
    return create_publication(
        caption,
        chosen,
        scheduled_at,
        user_id=user_id,
        calendar_event_id=calendar_event_id,
        calendar_event_title=calendar_event_title,
    )


@app.delete("/api/publications/{publication_id}", response_model=ScheduledPost)
def cancel_scheduled_publication(publication_id: str, request: Request):
    require_user(request)
    return cancel_publication(publication_id)


# Doppio metodo: il cron di Vercel chiama in GET, l'app usa POST con "Esegui ora".
@app.get("/api/publications/run", response_model=PublicationRunResult, include_in_schema=False)
@app.post("/api/publications/run", response_model=PublicationRunResult)
def run_scheduled_publications(request: Request):
    """Pubblica le voci scadute. Chiamabile dallo scheduler o dall'app aperta."""
    _require_scheduler(request)
    return process_due()


# Statici e media sono serviti dallo stesso processo locale.
app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")
app.mount("/", StaticFiles(directory=BASE_DIR / "frontend", html=True), name="frontend")

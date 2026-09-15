import requests
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse

from backend.config import BASE_DIR, STORAGE_DIR
from backend.models import AnalyticsSummary, AssetRecord, CalendarEventRequest, CalendarEventResponse, ContentDraft, ContentPublishResponse, ContentStatusRequest, CopyGenerationResponse, Opportunity
from backend.services.assets import asset_public_url, list_assets, save_uploads
from backend.services.analytics import calculate_analytics
from backend.services.copywriter import generate_copies
from backend.services.content import create_draft, list_drafts, update_status
from backend.services.radar import build_opportunities
from backend.adapters.instagram import publish_photo
from backend.config import USE_MOCK
from backend.adapters.google_calendar import authorization_url, create_event, exchange_code

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


@app.get("/api/analytics", response_model=AnalyticsSummary)
def get_analytics():
    # Il frontend riceve già dati ordinati e pronti per i grafici.
    return calculate_analytics()


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
    try:
        return build_opportunities(request.cookies.get("google_user"))
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Impossibile leggere il calendario: {error}") from error


@app.get("/api/content", response_model=list[ContentDraft])
def get_content():
    return list_drafts()


@app.patch("/api/content/{content_id}", response_model=ContentDraft)
def approve_content(content_id: str, request: ContentStatusRequest):
    return update_status(content_id, request.status)


@app.post("/api/content/{content_id}/publish", response_model=ContentPublishResponse)
def publish_content(content_id: str):
    drafts = list_drafts()
    draft = next((item for item in drafts if item.id == content_id), None)
    if not draft:
        raise HTTPException(status_code=404, detail="Contenuto non trovato.")
    if draft.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Approva il contenuto prima di pubblicarlo.")
    caption = draft.copies[0].caption if draft.copies else ""
    if USE_MOCK:
        update_status(content_id, "PUBLISHED")
        return ContentPublishResponse(id=content_id, status="PUBLISHED", platform_id=f"mock_{content_id[:8]}")
    try:
        platform_id = publish_photo(asset_public_url(draft.asset_ids[0]), caption)
    except (RuntimeError, requests.RequestException) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    update_status(content_id, "PUBLISHED")
    return ContentPublishResponse(id=content_id, status="PUBLISHED", platform_id=platform_id)


@app.get("/api/calendar/auth")
def calendar_auth():
    try:
        return {"url": authorization_url()}
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/calendar/callback")
def calendar_callback(code: str = Query(...)):
    try:
        user_id = exchange_code(code)
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail=f"Autenticazione Google fallita: {error}") from error
    response = RedirectResponse(url="/?google=connected", status_code=303)
    response.set_cookie("google_user", user_id, httponly=True, samesite="lax", max_age=2592000)
    return response


@app.post("/api/calendar/events", response_model=CalendarEventResponse)
def calendar_event(event: CalendarEventRequest, request: Request):
    user_id = request.cookies.get("google_user")
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

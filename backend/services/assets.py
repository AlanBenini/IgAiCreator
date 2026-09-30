import json
import os
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import HTTPException, UploadFile

from backend.config import BLOB_READ_WRITE_TOKEN, BLOB_STORE_ID, MEDIA_DIR, MAX_UPLOAD_BYTES, MAX_UPLOADS, VERCEL_RUNTIME
from backend.models import AssetRecord


ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MANIFEST_PATH = MEDIA_DIR / "uploaded_assets.json"
BLOB_PREFIX = "moment/assets/"


def _field(value, name: str):
    return value.get(name) if isinstance(value, dict) else getattr(value, name)


def _blob_client():
    from vercel.blob import AsyncBlobClient
    return AsyncBlobClient()


async def _blob_list():
    from vercel.blob import list_objects
    options = {"prefix": BLOB_PREFIX, "limit": 1000}
    if BLOB_READ_WRITE_TOKEN:
        options["token"] = BLOB_READ_WRITE_TOKEN
    if BLOB_STORE_ID:
        options["store_id"] = BLOB_STORE_ID
    return list_objects(**options)


def _blob_item_asset(item) -> AssetRecord | None:
    relative = _field(item, "pathname").removeprefix(BLOB_PREFIX)
    asset_id, separator, filename = relative.partition("/")
    filename = unquote(filename)
    if not separator or not asset_id or not filename:
        return None
    extension = Path(filename).suffix.lower()
    mime_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(extension)
    if not mime_type:
        return None
    return AssetRecord(id=asset_id, filename=filename, mime_type=mime_type, size=_field(item, "size"), url=_field(item, "url"))


def _read_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")


async def save_uploads(files: list[UploadFile]) -> list[AssetRecord]:
    if not files or len(files) > MAX_UPLOADS:
        raise HTTPException(status_code=400, detail=f"Carica da 1 a {MAX_UPLOADS} immagini.")

    assets = []
    for file in files:
        if file.content_type not in ALLOWED_TYPES:
            raise HTTPException(status_code=415, detail=f"Formato non supportato: {file.filename}.")
        content = await file.read()
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Ogni immagine deve pesare al massimo {MAX_UPLOAD_BYTES // 1048576} MB.")
        asset_id = uuid.uuid4().hex
        filename = f"{asset_id}{ALLOWED_TYPES[file.content_type]}"
        if VERCEL_RUNTIME:
            if not (BLOB_READ_WRITE_TOKEN or (BLOB_STORE_ID and os.getenv("VERCEL_OIDC_TOKEN"))):
                raise HTTPException(status_code=503, detail="Collega uno store Vercel Blob al progetto per salvare le immagini.")
            original_name = Path(file.filename or filename).name
            if Path(original_name).suffix.lower() != ALLOWED_TYPES[file.content_type]:
                original_name = f"{original_name}{ALLOWED_TYPES[file.content_type]}"
            pathname = f"{BLOB_PREFIX}{asset_id}/{original_name}"
            options = {"access": "public", "content_type": file.content_type, "add_random_suffix": False}
            if BLOB_READ_WRITE_TOKEN:
                options["token"] = BLOB_READ_WRITE_TOKEN
            if BLOB_STORE_ID:
                options["store_id"] = BLOB_STORE_ID
            try:
                blob = await _blob_client().put(pathname, content, **options)
            except Exception as error:
                raise HTTPException(status_code=502, detail=f"Salvataggio su Vercel Blob non riuscito: {error}") from error
            assets.append(AssetRecord(id=asset_id, filename=original_name, mime_type=file.content_type, size=len(content), url=_field(blob, "url")))
            continue

        manifest = _read_manifest()
        (MEDIA_DIR / filename).write_bytes(content)
        asset = AssetRecord(id=asset_id, filename=file.filename or filename, mime_type=file.content_type, size=len(content), url=f"/storage/media/{filename}")
        manifest[asset_id] = {**asset.model_dump(), "path": str(MEDIA_DIR / filename)}
        assets.append(asset)
        _write_manifest(manifest)
    return assets


def get_asset_files(assets: list[AssetRecord]) -> list[tuple[AssetRecord, Path | bytes]]:
    if VERCEL_RUNTIME:
        import asyncio

        async def fetch_all():
            catalog = await _blob_list()
            by_id = {asset.id: item for item in _field(catalog, "blobs") if (asset := _blob_item_asset(item))}
            client = _blob_client()
            result = []
            for asset in assets:
                item = by_id.get(asset.id)
                if not item:
                    raise HTTPException(status_code=404, detail=f"Immagine non trovata: {asset.filename}.")
                options = {"access": "public"}
                if BLOB_READ_WRITE_TOKEN:
                    options["token"] = BLOB_READ_WRITE_TOKEN
                response = await client.get(_field(item, "pathname"), **options)
                if not response or _field(response, "status_code") != 200 or _field(response, "stream") is None:
                    raise HTTPException(status_code=404, detail=f"Immagine non trovata: {asset.filename}.")
                content = b"".join([chunk async for chunk in _field(response, "stream")])
                result.append((asset, content))
            return result

        return asyncio.run(fetch_all())

    manifest = _read_manifest()
    files = []
    for asset in assets:
        item = manifest.get(asset.id)
        if not item or not Path(item["path"]).exists():
            raise HTTPException(status_code=404, detail=f"Immagine non trovata: {asset.filename}.")
        files.append((asset, Path(item["path"])))
    return files


def list_assets() -> list[AssetRecord]:
    if VERCEL_RUNTIME:
        import asyncio

        async def fetch():
            catalog = await _blob_list()
            return [asset for item in _field(catalog, "blobs") if (asset := _blob_item_asset(item))]

        return asyncio.run(fetch())
    return [AssetRecord(**item) for item in _read_manifest().values()]


def asset_public_url(asset_id: str) -> str:
    if VERCEL_RUNTIME:
        for asset in list_assets():
            if asset.id == asset_id:
                return asset.url
        raise HTTPException(status_code=404, detail="Asset non trovato.")
    item = _read_manifest().get(asset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Asset non trovato.")
    return item["url"]

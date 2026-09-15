import json
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from backend.config import MEDIA_DIR, MAX_UPLOAD_BYTES, MAX_UPLOADS
from backend.models import AssetRecord


ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MANIFEST_PATH = MEDIA_DIR / "uploaded_assets.json"


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

    manifest = _read_manifest()
    assets = []
    for file in files:
        if file.content_type not in ALLOWED_TYPES:
            raise HTTPException(status_code=415, detail=f"Formato non supportato: {file.filename}.")
        content = await file.read()
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"Ogni immagine deve pesare al massimo {MAX_UPLOAD_BYTES // 1048576} MB.")
        asset_id = uuid.uuid4().hex
        filename = f"{asset_id}{ALLOWED_TYPES[file.content_type]}"
        (MEDIA_DIR / filename).write_bytes(content)
        asset = AssetRecord(id=asset_id, filename=file.filename or filename, mime_type=file.content_type, size=len(content), url=f"/storage/media/{filename}")
        manifest[asset_id] = {**asset.model_dump(), "path": str(MEDIA_DIR / filename)}
        assets.append(asset)
    _write_manifest(manifest)
    return assets


def get_asset_files(assets: list[AssetRecord]) -> list[tuple[AssetRecord, Path]]:
    manifest = _read_manifest()
    files = []
    for asset in assets:
        item = manifest.get(asset.id)
        if not item or not Path(item["path"]).exists():
            raise HTTPException(status_code=404, detail=f"Immagine non trovata: {asset.filename}.")
        files.append((asset, Path(item["path"])))
    return files


def list_assets() -> list[AssetRecord]:
    return [AssetRecord(**item) for item in _read_manifest().values()]


def asset_public_url(asset_id: str) -> str:
    item = _read_manifest().get(asset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Asset non trovato.")
    return item["url"]
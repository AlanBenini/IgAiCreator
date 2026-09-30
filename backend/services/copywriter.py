import base64
import json
import re

import requests

from backend.config import EXPORTS_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from backend.models import AssetRecord, CopyItem
from backend.services.assets import get_asset_files


def _image_part(path, mime_type: str) -> dict:
    image_bytes = path if isinstance(path, bytes) else path.read_bytes()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}


def _parse_json(content: str, assets: list[AssetRecord]) -> list[CopyItem]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start:end + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = re.sub(r"([\[,])\s*(#[A-Za-z0-9_]+)\"", r'\1 "\2"', cleaned)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        data = json.loads(repaired)
    rows = data.get("copies", data) if isinstance(data, dict) else data
    return [CopyItem(number=index, hook=row.get("hook", ""), caption=row.get("caption", ""), hashtags=row.get("hashtags", [])) for index, row in enumerate(rows[:len(assets)], start=1)]


def generate_copies(assets: list[AssetRecord], theme: str | None = None, content_format: str = "instagram_post") -> list[CopyItem]:
    if not LLM_API_KEY:
        return [CopyItem(number=index, hook="Copy non configurato", caption=f"Configura LLM_API_KEY per generare il copy della foto {asset.filename}.", hashtags=[]) for index, asset in enumerate(assets, start=1)]

    instruction = f"Tema o tono richiesto: {theme}." if theme else "Mantieni un tono coerente con il brand e con il soggetto visivo."
    text = f"""Sei il Social Media Manager AI di un'azienda. Analizza le immagini allegate e prepara un contenuto {content_format}.
{instruction}
Restituisci esclusivamente JSON valido nel formato {{\"copies\":[{{\"hook\":\"...\",\"caption\":\"...\",\"hashtags\":[\"#...\"]}}]}}.
Devi restituire esattamente un elemento per ogni immagine, nello stesso ordine. Il caption deve essere pronto per la pubblicazione, concreto e senza inventare dettagli non visibili. Adatta tono, CTA e struttura al formato scelto."""
    content = [{"type": "text", "text": text}]
    for asset, path in get_asset_files(assets):
        content.append(_image_part(path, asset.mime_type))
    try:
        response = requests.post(
            f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": content}], "temperature": 0.7, "response_format": {"type": "json_object"}},
            timeout=90,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        (EXPORTS_DIR / "generated_copies.json").write_text(raw, encoding="utf-8")
        return _parse_json(raw, assets)
    except requests.HTTPError as error:
        detail = error.response.text[:500] if error.response is not None else str(error)
        raise RuntimeError(f"Impossibile generare il copy: {detail}") from error
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Impossibile generare il copy: {error}") from error

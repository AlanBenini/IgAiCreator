import requests
from backend.config import GRAPH_BASE_URL, ACCESS_TOKEN, IG_USER_ID, MEDIA_DIR, PUBLIC_BASE_URL


def download_file(url: str, filename: str) -> str:
    path = MEDIA_DIR / filename
    # Evita di riscaricare media già presenti nello storage.
    if path.exists():
        return f"/storage/media/{filename}"
    try:
        response = requests.get(url, stream=True, timeout=15)
        if response.status_code == 200:
            with open(path, "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
            return f"/storage/media/{filename}"
    except Exception as error:
        print(f"[Errore Download] {url}: {error}")
    return ""


def process_media_assets(post: dict) -> list:
    post_id = post["id"]
    saved_paths = []
    # Un carosello espone i file nei children, un post singolo nel media_url.
    if post.get("media_type") == "CAROUSEL_ALBUM" and "children" in post:
        for index, child in enumerate(post["children"].get("data", [])):
            child_url = child.get("media_url")
            if child_url:
                extension = "mp4" if child.get("media_type") == "VIDEO" else "jpg"
                path = download_file(child_url, f"{post_id}_slide_{index + 1}.{extension}")
                if path:
                    saved_paths.append(path)
    elif post.get("media_url"):
        extension = "mp4" if post.get("media_type") == "VIDEO" else "jpg"
        path = download_file(post["media_url"], f"{post_id}.{extension}")
        if path:
            saved_paths.append(path)
    return saved_paths


def fetch_graph_posts(limit: int = 25) -> list:
    endpoint = f"{GRAPH_BASE_URL}/{IG_USER_ID}/media"
    fields = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,children{id,media_type,media_url}"
    params = {"fields": fields, "limit": limit, "access_token": ACCESS_TOKEN}
    try:
        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        return response.json().get("data", [])
    except (requests.RequestException, ValueError) as error:
        print(f"[Errore Graph API] media: {error}")
        return []


def fetch_graph_insights(media_id: str) -> dict:
    endpoint = f"{GRAPH_BASE_URL}/{media_id}/insights"
    params = {"metric": "reach,saved,total_interactions", "access_token": ACCESS_TOKEN}
    try:
        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        return {item["name"]: item["values"][0]["value"] for item in payload.get("data", []) if item.get("values")}
    except (requests.RequestException, ValueError, KeyError, IndexError) as error:
        print(f"[Errore Graph API] insights {media_id}: {error}")
        return {}


def publish_photo(image_path: str, caption: str) -> str:
    if not PUBLIC_BASE_URL:
        raise RuntimeError("Configura PUBLIC_BASE_URL per pubblicare su Instagram con Graph API.")
    image_url = f"{PUBLIC_BASE_URL}{image_path}"
    container = requests.post(f"{GRAPH_BASE_URL}/{IG_USER_ID}/media", params={"image_url": image_url, "caption": caption, "access_token": ACCESS_TOKEN}, timeout=30)
    container.raise_for_status()
    creation_id = container.json()["id"]
    published = requests.post(f"{GRAPH_BASE_URL}/{IG_USER_ID}/media_publish", params={"creation_id": creation_id, "access_token": ACCESS_TOKEN}, timeout=30)
    published.raise_for_status()
    return published.json()["id"]

import requests

from backend.config import ACCESS_TOKEN, GRAPH_BASE_URL, IG_USER_ID, MEDIA_DIR, PUBLIC_BASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL


def _meta_connection_for_user(user_id: str | None) -> tuple[str, str]:
    if user_id and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        try:
            response = requests.get(
                f"{SUPABASE_URL}/rest/v1/meta_connections",
                params={"user_id": f"eq.{user_id}", "select": "page_access_token,instagram_user_id"},
                headers={
                    "apikey": SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                },
                timeout=15,
            )
            if response.status_code == 200:
                rows = response.json()
                if rows:
                    return rows[0].get("page_access_token", ACCESS_TOKEN), rows[0].get("instagram_user_id", IG_USER_ID)
        except requests.RequestException:
            pass
    return ACCESS_TOKEN, IG_USER_ID


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


def fetch_graph_posts(limit: int = 25, user_id: str | None = None) -> list:
    access_token, ig_user_id = _meta_connection_for_user(user_id)
    endpoint = f"{GRAPH_BASE_URL}/{ig_user_id}/media"
    fields = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count,children{id,media_type,media_url}"
    params = {"fields": fields, "limit": limit, "access_token": access_token}
    try:
        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        return response.json().get("data", [])
    except (requests.RequestException, ValueError) as error:
        print(f"[Errore Graph API] media: {error}")
        return []


def fetch_graph_insights(media_id: str, user_id: str | None = None) -> dict:
    access_token, _ = _meta_connection_for_user(user_id)
    endpoint = f"{GRAPH_BASE_URL}/{media_id}/insights"
    params = {"metric": "reach,saved,total_interactions", "access_token": access_token}
    try:
        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        return {item["name"]: item["values"][0]["value"] for item in payload.get("data", []) if item.get("values")}
    except (requests.RequestException, ValueError, KeyError, IndexError) as error:
        print(f"[Errore Graph API] insights {media_id}: {error}")
        return {}


def publish_photo(image_path: str, caption: str, user_id: str | None = None) -> str:
    access_token, ig_user_id = _meta_connection_for_user(user_id)
    if not PUBLIC_BASE_URL:
        raise RuntimeError("Configura PUBLIC_BASE_URL per pubblicare su Instagram con Graph API.")
    image_url = f"{PUBLIC_BASE_URL}{image_path}"
    container = requests.post(f"{GRAPH_BASE_URL}/{ig_user_id}/media", params={"image_url": image_url, "caption": caption, "access_token": access_token}, timeout=30)
    container.raise_for_status()
    creation_id = container.json()["id"]
    published = requests.post(f"{GRAPH_BASE_URL}/{ig_user_id}/media_publish", params={"creation_id": creation_id, "access_token": access_token}, timeout=30)
    published.raise_for_status()
    return published.json()["id"]

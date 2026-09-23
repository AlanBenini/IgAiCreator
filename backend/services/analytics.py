import json
import time

from backend.adapters.instagram import fetch_graph_posts, fetch_graph_insights, process_media_assets
from backend.adapters.mock_data import MOCK_POSTS_DATA
from backend.config import EXPORTS_DIR, INSIGHTS_CACHE_TTL, USE_MOCK
from backend.models import PostRecord, AnalyticsSummary


CACHE_FILE = EXPORTS_DIR / "insights_cache.json"


def _read_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(cache: dict) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        # La cache è solo un'ottimizzazione: se non è scrivibile l'analisi resta corretta.
        pass


def _insights_for(media_id: str, user_id: str | None, cache: dict) -> dict:
    entry = cache.get(media_id)
    if entry and entry.get("expires_at", 0) > time.time():
        return entry.get("insights", {})
    insights = fetch_graph_insights(media_id, user_id=user_id)
    cache[media_id] = {"expires_at": int(time.time()) + INSIGHTS_CACHE_TTL, "insights": insights}
    return insights


def diagnose_post(row: dict, averages: dict) -> tuple:
    strengths, weaknesses = [], []
    if row["save_rate"] >= averages["save_rate"]:
        strengths.append("Alto tasso di conservazione: percepito come risorsa pratica da salvare")
    else:
        weaknesses.append("Basso tasso di salvataggio: consumo rapido o carente di dati azionabili")
    if row["conv_rate"] >= averages["conv_rate"]:
        strengths.append("Forte incentivo al commento: CTA o tema hanno stimolato discussione")
    else:
        weaknesses.append("Bassa risposta della community: manca una domanda aperta o un gancio divisivo")
    if row["er"] >= averages["er"]:
        strengths.append("Efficienza algoritmica superiore: alto tasso di interazione su reach")
    else:
        weaknesses.append("Bassa ritenzione nel feed: ignorato senza azioni attive")
    return " • " + "\n • ".join(strengths), " • " + "\n • ".join(weaknesses)


def calculate_analytics(user_id: str | None = None) -> AnalyticsSummary:
    # La stessa pipeline funziona con dati demo o con la risposta di Meta.
    raw_posts = MOCK_POSTS_DATA if USE_MOCK else fetch_graph_posts(user_id=user_id)
    insights_cache = {} if USE_MOCK else _read_cache()
    if not raw_posts:
        return AnalyticsSummary(total_posts=0, avg_engagement_rate=0, avg_save_rate=0, avg_conversation_rate=0, total_reach=0, top_post_id="", posts=[])

    parsed_items = []
    for post in raw_posts:
        insights = post.get("insights", {}) if USE_MOCK else _insights_for(post["id"], user_id, insights_cache)
        reach = insights.get("reach", 0)
        likes = post.get("like_count", 0)
        comments = post.get("comments_count", 0)
        saved = insights.get("saved", 0)
        interactions = insights.get("total_interactions", likes + comments + saved)
        er = round(interactions / reach * 100, 2) if reach > 0 else 0.0
        save_rate = round(saved / reach * 100, 2) if reach > 0 else 0.0
        conv_rate = round(comments / reach * 100, 2) if reach > 0 else 0.0
        parsed_items.append({
            "id": post["id"], "media_type": post.get("media_type", "IMAGE"),
            "media_url": post.get("media_url", ""), "date": post.get("timestamp", "")[:10],
            "caption": post.get("caption", ""), "likes": likes, "comments": comments,
            "reach": reach, "saved": saved, "interactions": interactions, "er": er,
            "save_rate": save_rate, "conv_rate": conv_rate,
            "local_files": process_media_assets(post), "permalink": post.get("permalink", "")
        })

    count = len(parsed_items)
    # Le diagnosi confrontano ogni post con la media dell'account.
    averages = {
        "er": sum(item["er"] for item in parsed_items) / count if count else 0,
        "save_rate": sum(item["save_rate"] for item in parsed_items) / count if count else 0,
        "conv_rate": sum(item["conv_rate"] for item in parsed_items) / count if count else 0,
    }
    records = []
    for item in parsed_items:
        strengths, weaknesses = diagnose_post(item, averages)
        records.append(PostRecord(
            id=item["id"], media_type=item["media_type"], media_url=item["media_url"], date=item["date"],
            caption=item["caption"], likes=item["likes"], comments=item["comments"], reach=item["reach"],
            saved=item["saved"], interactions=item["interactions"], engagement_rate=item["er"],
            save_rate=item["save_rate"], conversation_rate=item["conv_rate"], strengths=strengths,
            weaknesses=weaknesses, local_files=item["local_files"], permalink=item["permalink"]
        ))
    if not USE_MOCK:
        _write_cache(insights_cache)
    records.sort(key=lambda record: record.engagement_rate, reverse=True)
    return AnalyticsSummary(
        total_posts=count, avg_engagement_rate=round(averages["er"], 2),
        avg_save_rate=round(averages["save_rate"], 2), avg_conversation_rate=round(averages["conv_rate"], 2),
        total_reach=sum(item["reach"] for item in parsed_items), top_post_id=records[0].id if records else "", posts=records
    )

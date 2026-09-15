from backend.config import USE_MOCK
from backend.adapters.mock_data import MOCK_POSTS_DATA
from backend.adapters.instagram import fetch_graph_posts, fetch_graph_insights, process_media_assets
from backend.models import PostRecord, AnalyticsSummary


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
    if not raw_posts:
        return AnalyticsSummary(total_posts=0, avg_engagement_rate=0, avg_save_rate=0, avg_conversation_rate=0, total_reach=0, top_post_id="", posts=[])

    parsed_items = []
    for post in raw_posts:
        insights = post.get("insights", {}) if USE_MOCK else fetch_graph_insights(post["id"], user_id=user_id)
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
    records.sort(key=lambda record: record.engagement_rate, reverse=True)
    return AnalyticsSummary(
        total_posts=count, avg_engagement_rate=round(averages["er"], 2),
        avg_save_rate=round(averages["save_rate"], 2), avg_conversation_rate=round(averages["conv_rate"], 2),
        total_reach=sum(item["reach"] for item in parsed_items), top_post_id=records[0].id if records else "", posts=records
    )

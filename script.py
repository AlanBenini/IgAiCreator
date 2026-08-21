import requests
import json
import pandas as pd
import os

API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")

def get_instagram_posts(user_id, token, limit=10):
    """Recupera la lista dei post recenti con i dati base."""
    endpoint = f"{BASE_URL}/{user_id}/media"
    params = {
        'fields': 'id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count',
        'limit': limit,
        'access_token': token
    }
    
    response = requests.get(endpoint, params=params)
    if response.status_code != 200:
        print(f"Errore nel recupero dei post: {response.json()}")
        return []
    
    return response.json().get('data', [])

def get_post_insights(media_id, media_type, token):
    """
    Recupera le metriche di insight per un singolo media.
    Le metriche supportate variano in base al tipo di post.
    """
    endpoint = f"{BASE_URL}/{media_id}/insights"
    
    # Metriche comuni (reach, saved, interazioni totali)
    # Per i VIDEO/REELS è presente anche plays
    metrics = ['reach', 'saved', 'total_interactions','plays'] if media_type in ['VIDEO', 'REELS'] else ['reach', 'saved', 'total_interactions']
    
    params = {
        'metric': ','.join(metrics),
        'access_token': token
    }
    
    response = requests.get(endpoint, params=params)
    insights_data = {}
    
    if response.status_code == 200:
        data = response.json().get('data', [])
        for item in data:
            insights_data[item['name']] = item['values'][0]['value']
    else:
        insights_data['error'] = response.json().get('error', {}).get('message', 'Errore')
        
    return insights_data

def main():
    print("Recupero dei post in corso...")
    posts = get_instagram_posts(IG_USER_ID, ACCESS_TOKEN, limit=5)
    
    all_data = []
    
    for post in posts:
        media_id = post['id']
        media_type = post.get('media_type')
        caption = post.get('caption', '').replace('\n', ' ')[:40] + "..." 
        
        print(f"Recupero statistiche per post: {media_id} ({media_type})")
        insights = get_post_insights(media_id, media_type, ACCESS_TOKEN)
        
        post_summary = {
            'ID': media_id,
            'Tipo': media_type,
            'Data': post.get('timestamp'),
            'Caption': caption,
            'Likes': post.get('like_count', 0),
            'Commenti': post.get('comments_count', 0),
            'Reach (Copertura)': insights.get('reach', 'N/D'),
            'Salvati': insights.get('saved', 'N/D'),
            'Interazioni Totali': insights.get('total_interactions', 'N/D'),
            'Plays': insights.get('plays', 'N/D'),
            'Link': post.get('permalink')
        }
        all_data.append(post_summary)
        
    df = pd.DataFrame(all_data)
    print("\n--- RISULTATI ---")
    print(df.to_string(index=False))
    
    df.to_csv("instagram_analytics.csv", index=False)
    print("\nDati salvati su 'instagram_analytics.csv'")

if __name__ == "__main__":
    main()
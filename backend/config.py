import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
# Vercel rende il filesystem del progetto non scrivibile; /tmp resta disponibile
# per la durata dell'istanza serverless. In locale continuiamo a usare storage/.
STORAGE_DIR = Path("/tmp/moment-storage") if os.getenv("VERCEL") else BASE_DIR / "storage"
MEDIA_DIR = STORAGE_DIR / "media"
EXPORTS_DIR = STORAGE_DIR / "exports"

for d in [STORAGE_DIR, MEDIA_DIR, EXPORTS_DIR]:
	d.mkdir(parents=True, exist_ok=True)

# I post demo sono la sorgente dati predefinita: finché non colleghiamo un
# account Meta reale, USE_MOCK deve restare attivo (anche in deploy).
USE_MOCK = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
# Le chiavi vuote sono valide in modalità mock.
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip().strip('"')
IG_USER_ID = os.getenv("IG_USER_ID", "").strip().strip('"')

# Provider compatibili con l'API OpenAI, come Groq e Ollama.
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip().strip('"')
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").strip().strip('"')
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b").strip().strip('"')
MAX_UPLOADS = int(os.getenv("MAX_UPLOADS", "20"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"')
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip('"')
VERCEL_HOST = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL", "")
VERCEL_BASE_URL = f"https://{VERCEL_HOST}" if VERCEL_HOST else ""
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", VERCEL_BASE_URL).strip().rstrip("/")
META_APP_ID = os.getenv("META_APP_ID", "").strip()
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
META_REDIRECT_URI = os.getenv(
	"META_REDIRECT_URI",
	f"{PUBLIC_BASE_URL}/api/auth/meta/callback" if PUBLIC_BASE_URL else "http://localhost:8000/api/auth/meta/callback",
).strip().strip('"')
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()

API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

# Cache degli insights Meta: evita una chiamata Graph API per post a ogni
# caricamento della vista Performance. TTL in secondi (default 6 ore).
INSIGHTS_CACHE_TTL = int(os.getenv("INSIGHTS_CACHE_TTL", "21600"))

# Il frontend è servito dallo stesso dominio del backend, quindi il default è
# nessuna origine esterna. Valorizza CORS_ORIGINS (lista separata da virgole)
# solo se esponi questa API a un frontend su un dominio diverso.
CORS_ORIGINS = [origin.strip().rstrip("/") for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]

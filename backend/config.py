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

USE_MOCK = os.getenv("USE_MOCK", "True").lower() in ("true", "1", "yes")
# Le chiavi vuote sono valide in modalità mock.
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip().strip('"')
IG_USER_ID = os.getenv("IG_USER_ID", "").strip().strip('"')

# Provider compatibili con l'API OpenAI, come Groq e Ollama.
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip().strip('"')
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").strip().strip('"')
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b").strip().strip('"')
IMAGE_API_KEY = os.getenv("IMAGE_API_KEY", "").strip().strip('"')
IMAGE_API_URL = os.getenv(
	"IMAGE_API_URL",
	"https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell",
).strip().strip('"')
MAX_UPLOADS = int(os.getenv("MAX_UPLOADS", "20"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"')
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip('"')
VERCEL_HOST = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL", "")
VERCEL_BASE_URL = f"https://{VERCEL_HOST}" if VERCEL_HOST else ""
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", VERCEL_BASE_URL).strip().rstrip("/")
GOOGLE_REDIRECT_URI = os.getenv(
	"GOOGLE_REDIRECT_URI",
	f"{PUBLIC_BASE_URL}/api/calendar/callback" if PUBLIC_BASE_URL else "http://localhost:8000/api/calendar/callback",
).strip().strip('"')
GOOGLE_TOKEN_DIR = STORAGE_DIR / "google_calendar_tokens"
GOOGLE_TOKEN_DIR.mkdir(parents=True, exist_ok=True)

API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

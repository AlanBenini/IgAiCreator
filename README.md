# MOMENT

AI content manager per Instagram: trasforma i momenti del business in contenuti pronti da approvare e pubblicare.

- **Backend** — FastAPI in `backend/`: analytics Meta, generazione copy con LLM su immagini, coda bozze, publishing su Instagram, Content Radar dagli eventi di Google Calendar.
- **Frontend** — HTML/CSS/JS vanilla in `frontend/`: nessun build step, nessun `package.json`.
- **Auth e dati utente** — Supabase (login Google lato client, tabelle `meta_connections` e `google_connections`).
- **Deploy** — Vercel: `api/index.py` espone FastAPI, `frontend/**` viene servito statico.

## Avvio locale

```bash
pip install -r requirements.txt
python run.py
```

`run.py` cerca una porta libera (8000-8009), apre il browser e serve API e frontend dallo stesso processo. Per avviare solo il server: `python -m uvicorn backend.main:app --port 8000`.

## Configurazione

Copia `.env.example` in `.env` e valorizza solo ciò che ti serve.

Senza `SUPABASE_URL` e `SUPABASE_ANON_KEY` il workspace parte in **modalità locale anonima**: niente login, gli endpoint protetti accettano chiamate anonime e i post demo di `backend/adapters/mock_data.py` sono la sorgente dati.

| Variabile | Default | A cosa serve |
|---|---|---|
| `USE_MOCK` | `True` | Usa i post demo invece della Graph API. Obbligatorio attivo finché non c'è un account Meta collegato |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | Groq + `qwen/qwen3.8-27b` | Endpoint OpenAI-compatibile per generare i copy (con vision sulle immagini) |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | vuoti | Login Google e validazione dei token utente. Se assenti, l'auth è disattivata |
| `SUPABASE_SERVICE_ROLE_KEY` | vuoto | Scrittura di `meta_connections` e `google_connections` (solo backend) |
| `META_APP_ID` / `META_APP_SECRET` / `META_REDIRECT_URI` | vuoti | OAuth Meta per collegare Instagram |
| `SESSION_SECRET` | vuoto | Firma HMAC dello `state` OAuth Meta (obbligatorio per collegare Instagram) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | vuoti | Rinnovo del token Google Calendar |
| `ACCESS_TOKEN` / `IG_USER_ID` | vuoti | Fallback token Meta globale, usato quando l'utente non ha un collegamento personale |
| `PUBLIC_BASE_URL` | derivato da Vercel | URL pubblico usato per consegnare le immagini a Meta in fase di pubblicazione |
| `MAX_UPLOADS` / `MAX_UPLOAD_BYTES` | `20` / `10 MB` | Limiti degli upload del Content Studio |
| `INSIGHTS_CACHE_TTL` | `21600` | Secondi di validità della cache degli insights Meta |
| `CORS_ORIGINS` | vuoto (nessuna origine esterna) | Lista separata da virgole, solo se il frontend è su un altro dominio |

### Tabelle Supabase

Esegui una volta nel SQL Editor del progetto: `supabase/meta_connections.sql` e `supabase/google_connections.sql`.

## API principali

| Metodo | Endpoint | Note |
|---|---|---|
| `GET` | `/api/analytics` | KPI e diagnosi dei post (`USE_MOCK=True` → dati demo) |
| `POST` | `/api/generate-copy` | Multipart: `files`, `theme`, `content_format`, `title`. Crea la bozza |
| `GET` | `/api/content`, `PATCH /api/content/{id}` | Coda bozze e cambio stato (`DRAFT`, `APPROVED`, `PUBLISHED`) |
| `POST` | `/api/content/{id}/publish` | Pubblica su Instagram se la bozza è `APPROVED` |
| `GET` | `/api/opportunities` | Eventi dei prossimi 30 giorni con priorità e brief di raccolta |
| `GET/POST` | `/api/calendar/events` | Lettura per intervallo e creazione evento |
| `POST` | `/api/calendar/events/recurring` | Serie settimanale o bisettimanale (`RRULE`, `Europe/Rome`) |
| `PATCH/DELETE` | `/api/calendar/events/{id}` | Modifica ed eliminazione singola |
| `POST` | `/api/calendar/events/bulk-delete` | Fino a 100 eventi per chiamata |
| `GET/POST` | `/api/calendar/status`, `/api/calendar/connect` | Stato e salvataggio del collegamento Google |

## Test

```bash
python -m unittest discover -s tests -t . -v
```

La suite usa solo la standard library: nessuna rete, nessuna credenziale, cartelle temporanee al posto di `storage/`.

## Limiti noti

- **Storage effimero su Vercel**: `backend/config.py` sposta `storage/` in `/tmp`; upload, bozze (`content_library.json`) e media non sopravvivono tra invocazioni. In locale il filesystem persiste, quindi la modalità supportata oggi è quella locale.
- **Modalità demo attiva**: con `USE_MOCK=True` (default) analytics e publishing usano i dati demo, non l'account reale.
- Il frontend non ha build né bundler: gli script sono caricati da CDN (Chart.js, supabase-js).

## Documentazione di progetto

- `PROJECT_BRIEF.md` — contesto durevole, stato corrente e prossimi passi.
- `DECISIONS.md` — decisioni tecniche con motivazione.

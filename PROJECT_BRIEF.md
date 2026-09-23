# PROJECT_BRIEF.md - MOMENT (IgAiCreator)

> Last updated: 23/09/2026

## 1. Goal and Users

Workspace per chi pubblica su Instagram: trasforma gli eventi e i materiali reali dell'azienda in contenuti approvati e pubblicati, con analytics e un radar di opportunità derivato dal calendario.

## 2. Current Scope

**In scope**
- Content Studio: upload → copy generato dall'LLM sulle immagini → bozza → approvazione → pubblicazione.
- Performance: KPI, grafici e diagnosi per post (dati demo, `USE_MOCK`).
- Content Radar: eventi Google Calendar → opportunità con priorità e brief di raccolta.
- Calendario settimanale: creazione, modifica, serie ricorrenti, eliminazione singola e multipla.
- Login Google via Supabase e collegamento Meta/Instagram per utente.

**Out of scope (per ora)**
- Rifinitura visiva della UI (prossimo lavoro concordato).
- Generazione immagini (la configurazione FLUX è stata rimossa finché la feature non esiste).
- Persistenza remota di upload e bozze (Supabase Storage).

## 3. Stack and Architecture

- Runtime: Python 3.13, FastAPI + uvicorn. Frontend HTML/CSS/JS vanilla, nessun build step.
- Librerie: pydantic v2, requests, python-dotenv, python-multipart; Chart.js e supabase-js da CDN.
- Dati: `storage/` (JSON + media; su Vercel diventa `/tmp`) e Supabase (`meta_connections`, `google_connections`).
- Deploy: Vercel serve `api/index.py` (FastAPI) e `frontend/**` (statici).
- Test: `python -m unittest discover -s tests -t . -v` (standard library, senza rete).

Flusso: browser → FastAPI (che serve anche `frontend/` e `/storage`) → Supabase (auth, token) / Meta Graph API / Google Calendar API / endpoint LLM.

## 4. Key Files

| Area | Path | Purpose |
|---|---|---|
| Route e auth | `backend/main.py` | Endpoint, gate `require_user`, OAuth Meta, mount statici |
| Config | `backend/config.py` | Variabili d'ambiente, storage, CORS, cache |
| Contratti | `backend/models.py` | Modelli pydantic di richieste e risposte |
| Analytics | `backend/services/analytics.py` | KPI, diagnosi e cache degli insights |
| Copy | `backend/services/copywriter.py` | Prompt e parsing del JSON dell'LLM |
| Bozze | `backend/services/content.py` | Coda `DRAFT` → `APPROVED` → `PUBLISHED` |
| Upload | `backend/services/assets.py` | Validazione MIME, limiti e manifest dei media |
| Radar | `backend/services/radar.py` | Eventi imminenti → opportunità |
| Instagram | `backend/adapters/instagram.py` | Graph API: media, insights, publish |
| Google | `backend/adapters/google_calendar.py` | Token, eventi, ricorrenze, bulk delete |
| Demo | `backend/adapters/mock_data.py` | 3 post demo: sorgente dati attuale |
| Frontend | `frontend/app.js`, `frontend/index.html` | 4 viste, calendario settimanale, auth |

## 5. How to Work

- Setup: `pip install -r requirements.txt` e `.env` copiato da `.env.example`.
- Run: `python run.py` (cerca una porta libera tra 8000 e 8009 e apre il browser).
- Test: `python -m unittest discover -s tests -t . -v`.
- Deploy: push su `main` → Vercel.
- Convenzioni: commenti in italiano, import della standard library prima dei moduli `backend.*`, funzioni brevi nei servizi e logging degli errori tradotto in messaggi utente.

## 6. Safety and Constraints

- `USE_MOCK=True` è la configurazione attuale obbligatoria: i post demo sono la sorgente dati finché non colleghiamo un account Meta reale.
- `.env` non è versionato: `SESSION_SECRET`, service role di Supabase e token Meta sono segreti.
- Su Vercel lo storage è effimero, quindi la modalità supportata oggi è quella locale.
- Ogni chiamata esterna ha timeout espliciti e gli errori vengono convertiti in messaggi comprensibili.

## 7. Current State

**Working**
- Shell a sidebar collassabile (248px ↔ 68px, drawer sotto i 900px) e design system unico in `frontend/style.css`.
- Analytics, Content Studio, coda bozze, radar, login Google, collegamento Meta.
- Calendario settimanale operativo: schermata con scorrimento all'orario del primo evento, header e asse delle ore ancorati, eventi sovrapposti affiancati in corsie, banda per gli eventi di giornata intera, evidenziazione corretta del giorno corrente e linea dell'ora attuale (rifatto il 23/09/2026, vedi D-014).

**Known issues**
- Storage effimero su Vercel (vedi README, sezione "Limiti noti").
- Pubblicazione reale mai verificata end-to-end: serve un account IG business e un `PUBLIC_BASE_URL` raggiungibile da Meta.
- Calendario e Meta sono verificabili solo con Supabase configurato: in locale restano stati vuoti.

**Next**
- Riscontro del committente vista per vista (Today, Opportunities, Content Studio, Performance).

## 8. Team and Handoff

- Producer: scope, priorità e accettazione.
- Dev: implementazione, test e verifica.
- QA: verifica indipendente quando il rischio la giustifica.

## Where to record decisions

`DECISIONS.md` nella root del progetto.

# DECISIONS

> Formato: titolo, data, owner, decisione in una riga, motivazione, alternative scartate.

## D-001 - Auth a gate condizionale - 23/09/2026 - Cline

**Decisione:** `require_user` esige un Bearer valido quando Supabase è configurato, mentre in locale (senza `SUPABASE_URL`) accetta chiamate anonime.
**Motivazione:** protegge gli endpoint esposti su Vercel — `POST /api/generate-copy` consuma credito LLM — senza rompere la demo locale, che non ha login configurato.
**Alternative scartate:** auth obbligatoria sempre (rompe la demo locale e i test); nessun gate (endpoint aperti in produzione).

## D-002 - CORS configurabile invece del wildcard - 23/09/2026 - Cline

**Decisione:** nessuna origine esterna per default; `CORS_ORIGINS` fornisce la lista; `*` resta possibile solo esplicitamente e senza credenziali.
**Motivazione:** il frontend è servito dallo stesso dominio, quindi la lista vuota basta; wildcard insieme alle credenziali non è conforme alle specifiche.

## D-003 - Rimozione dell'OAuth Google duplicato - 23/09/2026 - Cline

**Decisione:** eliminati `authorization_url`, `exchange_code`, `SCOPES`, `GOOGLE_REDIRECT_URI`, `GOOGLE_TOKEN_DIR` e il riferimento alla route inesistente `/api/calendar/callback`.
**Motivazione:** il consenso Google passa da Supabase Auth nel frontend; quel codice non era raggiungibile da nessuna route e la redirect URI puntava a un endpoint mai implementato.

## D-004 - Rimozione della configurazione di generazione immagini - 23/09/2026 - Cline

**Decisione:** eliminati `IMAGE_API_KEY` e `IMAGE_API_URL` (FLUX) finché la feature non esiste.
**Motivazione:** configurazione mai letta da nessun modulo: suggeriva una capacità inesistente. Da reintrodurre insieme all'endpoint di generazione immagini.

## D-005 - I post demo restano obbligatori - 23/09/2026 - Utente

**Decisione:** `USE_MOCK` resta `True` di default e `backend/adapters/mock_data.py` continua a essere la sorgente dati.
**Motivazione:** indicazione esplicita dell'utente ("per ora i mock files dei post dobbiamo obbligatoriamente usarli").
**Mitigazione:** all'avvio il backend stampa un warning che segnala la modalità demo; un test verifica che il dataset demo esista e sia completo.

## D-006 - Cache degli insights Meta - 23/09/2026 - Cline

**Decisione:** gli insights per post vengono memorizzati su file (`INSIGHTS_CACHE_TTL`, default 6 ore) e riusati.
**Motivazione:** la vista Performance effettuava una chiamata Graph API per post a ogni caricamento; la cache rende l'operazione ripetibile senza costi.

## D-007 - Storage: locale supportato, Vercel documentato - 23/09/2026 - Cline

**Decisione:** nessuna migrazione a storage remoto in questo momento; il limite viene documentato (README, PROJECT_BRIEF) e stampato all'avvio quando gira su Vercel.
**Motivazione:** spostare upload e bozze su Supabase Storage è una feature a sé, non verificabile senza credenziali reali; va pianificata con il committente.
**Follow-up:** valutare Supabase Storage per media e una tabella `content_drafts`.

## D-008 - Test con `unittest` della standard library - 23/09/2026 - Cline

**Decisione:** la suite vive in `tests/` e usa `unittest`.
**Motivazione:** `pytest` non è installato e `fastapi.testclient` richiederebbe `httpx2`; `unittest` è disponibile subito e non aggiunge dipendenze. I test non toccano la rete: usano i post demo, mock di `requests` e cartelle temporanee.
**Limite:** nessun test end-to-end degli endpoint HTTP, che resta una verifica manuale.

## D-009 - Token Google scaduto senza refresh token - 23/09/2026 - Cline

**Decisione:** `_access_token` solleva un errore esplicito ("ricollega il calendario") invece di inviare a Google un token non più valido; il rinnovo continua a essere salvato su Supabase.
**Motivazione:** il 401 di Google era opaco per l'utente e il rinnovo veniva già persistito, quindi mancava solo il caso senza `refresh_token`.

## D-010 - Redesign della shell: un solo design system - 23/09/2026 - Cline

**Decisione:** `frontend/style.css` riscritto da zero come unica fonte di verità: token semantici, radius 8px, testo corrente 13px (minimo 11px), accento blu riservato ad azione primaria, voce attiva e link. La navigazione passa dalle tre barre orizzontali a una sidebar laterale collassabile (248px ↔ 68px, stato in `localStorage`) con drawer sotto i 900px; account e stato del workspace in basso a sinistra.
**Motivazione:** il foglio conteneva quattro temi sovrapposti (dark glass, light pass, work tool, top navigation) con `--coral` ridefinito a `#2563eb` e decine di colori hardcoded: da lì il "feel AI-generated". Riferimenti usati: nuova dashboard Buffer (navigazione verticale, avatar in basso a sinistra) e spec App Side Nav di Helios (icone che ereditano il colore della voce, collapse con toggle, overlay su viewport piccole).
**Alternative scartate:** rifinire i layer esistenti (le regole si sovrascrivevano), cambiare palette (indicazione del committente: quella attuale va bene).

## D-011 - Fix: il calendario settimanale non veniva costruito - 23/09/2026 - Cline

**Decisione:** aggiunto `id="calendar-planner"` al pannello di pianificazione, aggiunto il pulsante di chiusura `close-calendar-planner` (referenziato in `app.js` ma mai presente nel markup) e un pulsante "+ Nuova serie" nella toolbar per riaprire il pianificatore.
**Motivazione:** `setupCalendarShell()` cerca `$('calendar-planner')` per id, ma il markup aveva solo la classe: la shell del calendario (griglia settimanale, editor eventi, eliminazione multipla) non veniva mai creata. Verificato con `git show HEAD:frontend/index.html`: il difetto era già nel commit `993a472`, quindi non è stato introdotto dal redesign. Senza il pulsante di chiusura, una volta nascosto il pianificatore non sarebbe più stato raggiungibile.

## D-012 - Stati vuoti onesti al posto degli errori - 23/09/2026 - Cline

**Decisione:** un 401 su `/api/calendar/status` viene trattato come "non collegato" e mostrato con uno stato progettato ("Google Calendar non collegato" + azione di collegamento), non come errore; quando il login non è configurato il messaggio lo dice esplicitamente. Numeri formattati con `toLocaleString('it-IT')` (nota: in italiano CLDR il separatore delle migliaia parte da 5 cifre, quindi `9100` resta senza punto).
**Motivazione:** l'utente vedeva "Accedi prima con Google." dentro un box tratteggiato, indistinguibile da un guasto.

## D-013 - Cache del browser: asset versionati e rivalidazione forzata - 23/09/2026 - Cline

**Decisione:** aggiunto un middleware che imposta `Cache-Control: no-cache` su tutte le risposte (default, sovrascrivibile) e riferimenti versionati nel markup (`style.css?v=2`, `app.js?v=2`).
**Motivazione:** dopo il redesign l'utente vedeva l'HTML nuovo con il CSS/JS vecchi in cache: il markup della sidebar appariva come testo sciolto e i pulsanti erano invisibili. Il server locale non inviava alcun `Cache-Control` (verificato: header vuoto), quindi Chrome applicava la freshness euristica (circa il 10% dell'età del file) e riusava i vecchi asset per ore. Il file servito era già corretto (26.713 byte, `.stat-card` presente): non era un problema di contenuto ma di cache. Con `no-cache` il browser conserva i file ma li rivalida a ogni richiesta (304 se invariati), quindi non ci sono più render misti.
**Alternative scartate:** `max-age` breve (lascia finestre di incoerenza), `no-store` (disabilita del tutto la cache, inutile per asset statici).


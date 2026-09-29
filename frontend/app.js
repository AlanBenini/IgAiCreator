let erChartInstance = null;
let matrixChartInstance = null;
let selectedFiles = [];
let supabaseClient = null;
let calendarMonth = new Date();
let selectedCalendarEvents = new Set();
// Stato del piano editoriale e della coda di pubblicazione.
let appConfig = { storage_ephemeral: false, scheduler_configured: false };
let editorialSuggestions = [];
let pendingAssetIds = [];
let linkedCalendarEvent = null;
const WEEKDAY_SHORT = { MO: 'Lun', TU: 'Mar', WE: 'Mer', TH: 'Gio', FR: 'Ven', SA: 'Sab', SU: 'Dom' };
const PUBLICATION_LABELS = { SCHEDULED: 'In coda', PUBLISHED: 'Pubblicato', FAILED: 'Non riuscito', CANCELLED: 'Annullato' };
const $ = id => document.getElementById(id);

function renderTodayDate() {
  const target = $('today-date');
  if (!target) return;
  target.textContent = new Date().toLocaleDateString('it-IT', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).toUpperCase();
}

function initSidebar() {
  const shell = $('app-shell');
  if (!shell) return;
  if (window.localStorage.getItem('moment.sidebar') === 'collapsed') shell.classList.add('collapsed');
  const toggle = $('sidebar-toggle');
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(!shell.classList.contains('collapsed')));
    toggle.addEventListener('click', () => {
      const collapsed = shell.classList.toggle('collapsed');
      toggle.setAttribute('aria-expanded', String(!collapsed));
      window.localStorage.setItem('moment.sidebar', collapsed ? 'collapsed' : 'expanded');
    });
  }
  const backdrop = $('sidebar-backdrop');
  const closeDrawer = () => { shell.classList.remove('drawer-open'); backdrop?.classList.add('hidden'); };
  $('sidebar-open')?.addEventListener('click', () => { shell.classList.add('drawer-open'); backdrop?.classList.remove('hidden'); });
  backdrop?.addEventListener('click', closeDrawer);
  document.querySelectorAll('.nav-btn').forEach(button => button.addEventListener('click', closeDrawer));
}

function emptyState(title, text, actionLabel, action) {
  const wrapper = document.createElement('div');
  wrapper.className = 'empty-state';
  const heading = document.createElement('strong');
  heading.textContent = title;
  const paragraph = document.createElement('p');
  paragraph.textContent = text;
  wrapper.append(heading, paragraph);
  if (actionLabel && action) {
    const button = document.createElement('button');
    button.className = 'btn btn-primary';
    button.type = 'button';
    button.textContent = actionLabel;
    button.addEventListener('click', action);
    wrapper.appendChild(button);
  }
  return wrapper;
}

function renderCalendarState(connected) {
  const state = $('today-calendar-state');
  const hint = $('today-calendar-hint');
  if (!state || !hint) return;
  state.textContent = connected ? 'Collegato' : 'Non collegato';
  hint.textContent = connected ? 'Eventi e opportunità aggiornati' : 'Collega Google Calendar per il radar eventi';
  state.closest('.stat-card')?.classList.toggle('is-muted', !connected);
}

function calendarPrompt() {
  if (supabaseClient) {
    return emptyState('Google Calendar non collegato', 'Collega il calendario per ricevere opportunità e brief prima dei tuoi eventi.', 'Collega Google Calendar', connectGoogle);
  }
  return emptyState('Login non configurato', 'Configura Supabase per collegare Google Calendar e usare il Content Radar.');
}

async function initializeAuth() {
  const response = await fetch('/api/config');
  const config = await response.json();
  appConfig = config;
  renderPublicationNote();
  if (!config.supabase_url || !config.supabase_anon_key || !window.supabase) {
    $('auth-status').textContent = 'Login non configurato';
    return;
  }
  supabaseClient = window.supabase.createClient(config.supabase_url, config.supabase_anon_key);
  const { data: { session } } = await supabaseClient.auth.getSession();
  updateAuthUI(session);
  await syncGoogleCalendar(session);
  supabaseClient.auth.onAuthStateChange(async (_event, nextSession) => { updateAuthUI(nextSession); await syncGoogleCalendar(nextSession); });
}
function updateAuthUI(session) {
  const user = session?.user;
  $('auth-status').textContent = user ? `Connesso: ${user.email || 'account Google'}` : 'Accedi per usare il workspace';
  $('login-google').classList.toggle('hidden', Boolean(user));
  $('meta-connect').classList.toggle('hidden', !user);
  $('logout').classList.toggle('hidden', !user);
}
async function loginWithGoogle() {
  if (!supabaseClient) return status('workspace-status', 'Configura Supabase su Vercel prima di accedere.', 'error');
  await supabaseClient.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin, scopes: 'https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events', queryParams: { access_type: 'offline', prompt: 'consent', include_granted_scopes: 'true' } } });
}
async function logout() { if (supabaseClient) await supabaseClient.auth.signOut(); }

function switchView(view) {
  document.querySelectorAll('.view').forEach(element => element.classList.toggle('active', element.id === `view-${view}`));
  document.querySelectorAll('.nav-btn').forEach(element => element.classList.toggle('active', element.dataset.view === view));
  if (view === 'today') loadHome();
  if (view === 'opportunities') { loadOpportunities('opportunity-list'); loadCalendarMonth(); loadEditorialSuggestions(); loadEditorialPlans(); }
  if (view === 'content') { loadContent(); loadPublications(); }
  if (view === 'performance') loadAnalytics();
}
function status(id, message, type = '') { const element = $(id); element.textContent = message; element.className = `status-msg ${type}`; }
function priorityClass(priority) { return priority === 'ALTA' ? 'priority-high' : priority === 'MEDIA' ? 'priority-medium' : 'priority-low'; }
function renderOpportunity(item) { const element = document.createElement('article'); element.className = 'opportunity-card'; element.innerHTML = `<div class="opportunity-top"><span class="priority ${priorityClass(item.priority)}">${item.priority}</span><span class="opportunity-date">${item.start.slice(0, 16).replace('T', ' ')}</span></div><h3></h3><p></p><div class="brief-list"></div>`; element.querySelector('h3').textContent = item.title; element.querySelector('p').textContent = item.reason; item.capture_brief.forEach(line => { const brief = document.createElement('span'); brief.textContent = line; element.querySelector('.brief-list').appendChild(brief); }); return element; }
async function readResponse(response) { const raw = await response.text(); try { return raw ? JSON.parse(raw) : {}; } catch { return { detail: raw || `Errore HTTP ${response.status}` }; } }
async function loadOpportunities(targetId = 'today-opportunities') {
  const container = $(targetId);
  if (!container) return;
  container.replaceChildren();
  try {
    const headers = await authHeaders();
    const connectionResponse = await fetch('/api/calendar/status', { headers });
    const connection = await readResponse(connectionResponse);
    // 401 significa solo "nessun account collegato": non è un errore da mostrare all'utente.
    if (connectionResponse.status === 401) {
      renderCalendarState(false);
      container.appendChild(calendarPrompt());
      return;
    }
    if (!connectionResponse.ok) throw new Error(connection.detail || 'Stato Google Calendar non disponibile.');
    renderCalendarState(Boolean(connection.connected));
    if (!connection.connected) {
      container.appendChild(calendarPrompt());
      return;
    }
    const response = await fetch('/api/opportunities', { headers });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Calendario non disponibile.');
    if (!data.length) {
      container.appendChild(emptyState('Nessun evento in arrivo', 'Nelle prossime 4 settimane il calendario è libero: pianifica un evento per generare nuove opportunità.'));
      return;
    }
    data.forEach(item => container.appendChild(renderOpportunity(item)));
  } catch (error) {
    container.appendChild(emptyState('Radar non disponibile', error.message));
  }
}
function calendarDateKey(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }
function localDateTimeValue(date, hour) { const copy = new Date(date); copy.setHours(hour, 0, 0, 0); const offset = copy.getTimezoneOffset() * 60000; return new Date(copy.getTime() - offset).toISOString().slice(0, 16); }
function openCalendarPlanner(date) { const planner = $('calendar-planner'); if (!planner) return; planner.classList.remove('hidden'); $('schedule-start').value = localDateTimeValue(date, 9); $('schedule-end').value = localDateTimeValue(date, 10); const weekday = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'][date.getDay()]; document.querySelectorAll('input[name="schedule-day"]').forEach(input => { input.checked = input.value === weekday; }); planner.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function weekStart(date) { const start = new Date(date); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); return start; }
function formatEventInput(raw) { if (!raw) return ''; if (raw.length === 10) return `${raw}T09:00`; const date = new Date(raw); const offset = date.getTimezoneOffset() * 60000; return new Date(date.getTime() - offset).toISOString().slice(0, 16); }
function openEventEditor(calendarEvent) { let editor = $('calendar-event-editor'); if (!editor) { editor = document.createElement('section'); editor.id = 'calendar-event-editor'; editor.className = 'calendar-event-editor panel'; $('calendar-grid').parentNode.after(editor); } editor.innerHTML = '<div class="panel-heading"><div><span class="step">EVENTO</span><h2>Modifica evento</h2></div><button class="icon-button" type="button" id="close-event-editor" aria-label="Chiudi">&times;</button></div><form id="calendar-edit-form" class="event-edit-form"><label>Titolo<input id="edit-event-title" type="text" required></label><label>Descrizione<textarea id="edit-event-description" rows="3"></textarea></label><div class="schedule-time"><label>Inizio<input id="edit-event-start" type="datetime-local" required></label><label>Fine<input id="edit-event-end" type="datetime-local" required></label></div><div class="event-editor-actions"><button class="btn btn-danger" id="delete-event-button" type="button">Elimina</button><button class="btn btn-primary" type="submit">Salva modifiche</button></div><div id="event-editor-status" class="status-msg hidden"></div></form>'; $('edit-event-title').value = calendarEvent.summary || ''; $('edit-event-description').value = calendarEvent.description || ''; $('edit-event-start').value = formatEventInput(calendarEvent.start?.dateTime || calendarEvent.start?.date); $('edit-event-end').value = formatEventInput(calendarEvent.end?.dateTime || calendarEvent.end?.date); $('close-event-editor').onclick = () => editor.remove(); $('delete-event-button').onclick = () => { if (window.confirm(`Eliminare '${calendarEvent.summary || 'Evento'}'?`)) deleteCalendarEvent(calendarEvent.id).then(() => editor.remove()); }; $('calendar-edit-form').onsubmit = async event => { event.preventDefault(); const message = $('event-editor-status'); try { const response = await fetch(`/api/calendar/events/${encodeURIComponent(calendarEvent.id)}`, { method: 'PATCH', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('edit-event-title').value.trim(), description: $('edit-event-description').value.trim(), start: `${$('edit-event-start').value}:00`, end: `${$('edit-event-end').value}:00` }) }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Modifica non riuscita.'); message.textContent = 'Evento aggiornato.'; message.className = 'status-msg success'; await loadCalendarMonth(); await loadOpportunities('opportunity-list'); } catch (error) { message.textContent = error.message; message.className = 'status-msg error'; } }; editor.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
async function deleteCalendarEvent(eventId) { try { const response = await fetch(`/api/calendar/events/${encodeURIComponent(eventId)}`, { method: 'DELETE', headers: await authHeaders() }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Eliminazione non riuscita.'); await loadCalendarMonth(); await loadOpportunities('opportunity-list'); } catch (error) { status('workspace-status', error.message, 'error'); } }
function formatEventHour(minutes) {
  const hours = Math.floor(minutes / 60);
  return `${String(hours).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

function packOverlappingEvents(items) {
  const sorted = items.sort((a, b) => a.startMinutes - b.startMinutes || b.endMinutes - a.endMinutes);
  const packed = [];
  let cluster = [];
  let clusterEnd = -1;
  const flush = () => {
    const laneEnds = [];
    cluster.forEach(item => {
      let lane = laneEnds.findIndex(end => end <= item.startMinutes);
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(0); }
      laneEnds[lane] = item.endMinutes;
      item.lane = lane;
    });
    cluster.forEach(item => { item.lanes = laneEnds.length; });
    packed.push(...cluster);
    cluster = [];
    clusterEnd = -1;
  };
  sorted.forEach(item => {
    if (cluster.length && item.startMinutes >= clusterEnd) flush();
    cluster.push(item);
    clusterEnd = Math.max(clusterEnd, item.endMinutes);
  });
  if (cluster.length) flush();
  return packed;
}

function createSelectionCheckbox(calendarEvent) {
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'calendar-select';
  checkbox.checked = selectedCalendarEvents.has(calendarEvent.id);
  checkbox.setAttribute('aria-label', `Seleziona ${calendarEvent.summary || 'evento'}`);
  checkbox.addEventListener('click', event => event.stopPropagation());
  checkbox.addEventListener('change', event => {
    if (event.target.checked) selectedCalendarEvents.add(calendarEvent.id);
    else selectedCalendarEvents.delete(calendarEvent.id);
    updateBulkDeleteButton();
  });
  return checkbox;
}

function renderCalendar(events = []) {
  const grid = $('calendar-grid');
  if (!grid) return;
  const weekStartDate = weekStart(calendarMonth);
  const weekEndDate = new Date(weekStartDate);
  weekEndDate.setDate(weekStartDate.getDate() + 7);
  const lastDay = new Date(weekEndDate - 86400000);
  const label = $('calendar-month-label');
  if (label) {
    const rangeFormat = weekStartDate.getMonth() === lastDay.getMonth() ? { day: 'numeric' } : { day: 'numeric', month: 'short' };
    label.textContent = `${weekStartDate.toLocaleDateString('it-IT', rangeFormat)} - ${lastDay.toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })}`;
  }
  const dayCells = [];
  for (let index = 0; index < 7; index += 1) {
    const date = new Date(weekStartDate);
    date.setDate(weekStartDate.getDate() + index);
    dayCells.push({ index, date, key: calendarDateKey(date) });
  }
  const todayKey = calendarDateKey(new Date());
  grid.replaceChildren();

  const header = document.createElement('div');
  header.className = 'calendar-week-header';
  header.appendChild(document.createElement('span'));
  dayCells.forEach(cell => {
    const head = document.createElement('div');
    head.className = cell.key === todayKey ? 'calendar-day-head is-today' : 'calendar-day-head';
    const name = document.createElement('span');
    name.className = 'calendar-day-name';
    name.textContent = ['LUN', 'MAR', 'MER', 'GIO', 'VEN', 'SAB', 'DOM'][cell.index];
    const number = document.createElement('span');
    number.className = 'calendar-day-number';
    number.textContent = String(cell.date.getDate());
    head.append(name, number);
    header.appendChild(head);
  });
  grid.appendChild(header);

  const timedEvents = events.filter(calendarEvent => calendarEvent?.start?.dateTime);
  const allDayEvents = events.filter(calendarEvent => calendarEvent?.start?.date && !calendarEvent.start.dateTime);

  if (allDayEvents.length) {
    const band = document.createElement('div');
    band.className = 'calendar-allday';
    const bandLabel = document.createElement('span');
    bandLabel.className = 'calendar-allday-label';
    bandLabel.textContent = 'Tutto il giorno';
    band.appendChild(bandLabel);
    dayCells.forEach(cell => {
      const slot = document.createElement('div');
      slot.className = 'calendar-allday-day';
      allDayEvents.forEach(calendarEvent => {
        const startDate = new Date(`${calendarEvent.start.date}T00:00:00`);
        const endDate = calendarEvent.end?.date ? new Date(`${calendarEvent.end.date}T00:00:00`) : new Date(startDate.getTime() + 86400000);
        if (cell.date < startDate || cell.date >= endDate) return;
        const item = document.createElement('div');
        item.className = 'calendar-allday-item';
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'calendar-allday-chip';
        chip.textContent = calendarEvent.summary || 'Evento';
        chip.title = 'Modifica evento';
        chip.addEventListener('click', event => { event.stopPropagation(); openEventEditor(calendarEvent); });
        item.append(createSelectionCheckbox(calendarEvent), chip);
        slot.appendChild(item);
      });
      band.appendChild(slot);
    });
    grid.appendChild(band);
  }

  const body = document.createElement('div');
  body.className = 'calendar-week-body';
  const timeAxis = document.createElement('div');
  timeAxis.className = 'calendar-time-axis';
  for (let hour = 0; hour < 24; hour += 1) {
    const tick = document.createElement('span');
    tick.textContent = `${String(hour).padStart(2, '0')}:00`;
    timeAxis.appendChild(tick);
  }
  body.appendChild(timeAxis);

  const columns = [];
  dayCells.forEach(cell => {
    const column = document.createElement('div');
    column.className = cell.key === todayKey ? 'calendar-day-column is-today' : 'calendar-day-column';
    column.addEventListener('click', event => {
      if (event.target !== column) return;
      const rect = column.getBoundingClientRect();
      const date = new Date(cell.date);
      date.setHours(Math.min(23, Math.max(0, Math.floor((event.clientY - rect.top) / 60))), 0, 0, 0);
      openCalendarPlanner(date);
    });
    columns.push(column);
    body.appendChild(column);
  });

  let earliestMinutes = null;
  dayCells.forEach(cell => {
    const nextDay = new Date(cell.date);
    nextDay.setDate(cell.date.getDate() + 1);
    const items = timedEvents.filter(calendarEvent => {
      const eventStart = new Date(calendarEvent.start.dateTime);
      return eventStart >= cell.date && eventStart < nextDay;
    }).map(calendarEvent => {
      const eventStart = new Date(calendarEvent.start.dateTime);
      const eventEnd = new Date(calendarEvent.end?.dateTime || calendarEvent.start.dateTime);
      const startMinutes = eventStart.getHours() * 60 + eventStart.getMinutes();
      const rawEnd = eventEnd > nextDay ? 1440 : eventEnd.getHours() * 60 + eventEnd.getMinutes();
      return { calendarEvent, startMinutes, endMinutes: Math.min(1440, Math.max(startMinutes + 30, rawEnd)) };
    });
    packOverlappingEvents(items).forEach(item => {
      const duration = item.endMinutes - item.startMinutes;
      const laneWidth = 100 / item.lanes;
      const row = document.createElement('div');
      row.className = duration < 46 ? 'calendar-timed-event-row is-compact' : 'calendar-timed-event-row';
      row.style.top = `${item.startMinutes}px`;
      row.style.height = `${duration}px`;
      row.style.left = `${item.lane * laneWidth}%`;
      row.style.width = `${laneWidth}%`;
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'calendar-timed-event';
      card.title = `${formatEventHour(item.startMinutes)} - ${formatEventHour(item.endMinutes)} ${item.calendarEvent.summary || 'Evento'}`;
      card.addEventListener('click', event => { event.stopPropagation(); openEventEditor(item.calendarEvent); });
      const title = document.createElement('strong');
      title.textContent = item.calendarEvent.summary || 'Evento';
      const time = document.createElement('span');
      time.className = 'calendar-event-time';
      time.textContent = `${formatEventHour(item.startMinutes)} - ${formatEventHour(item.endMinutes)}`;
      card.append(title, time);
      row.append(card, createSelectionCheckbox(item.calendarEvent));
      columns[cell.index].appendChild(row);
      if (earliestMinutes === null || item.startMinutes < earliestMinutes) earliestMinutes = item.startMinutes;
    });
    if (cell.key === todayKey) {
      const now = new Date();
      const marker = document.createElement('div');
      marker.className = 'calendar-now';
      marker.style.top = `${now.getHours() * 60 + now.getMinutes()}px`;
      const dot = document.createElement('span');
      dot.className = 'calendar-now-dot';
      marker.appendChild(dot);
      columns[cell.index].appendChild(marker);
    }
  });

  grid.appendChild(body);
  grid.style.setProperty('--calendar-head-h', `${header.offsetHeight}px`);
  updateBulkDeleteButton();

  // riporto la vista sulle ore lavorative solo quando cambia settimana, così non
  // annullo lo scorrimento scelto dall'utente a ogni aggiornamento
  if (grid.dataset.week !== calendarDateKey(weekStartDate)) {
    grid.dataset.week = calendarDateKey(weekStartDate);
    grid.scrollTop = earliestMinutes === null ? 8 * 60 : Math.max(0, earliestMinutes - 60);
  }
}
async function deleteSelectedCalendarEvents() { const eventIds = [...selectedCalendarEvents]; if (!eventIds.length) return; if (!window.confirm(`Eliminare ${eventIds.length} eventi selezionati?`)) return; try { const response = await fetch('/api/calendar/events/bulk-delete', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ event_ids: eventIds }) }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Eliminazione multipla non riuscita.'); selectedCalendarEvents.clear(); await loadCalendarMonth(); await loadOpportunities('opportunity-list'); status('workspace-status', `${data.count} eventi eliminati.`, 'success'); } catch (error) { status('workspace-status', error.message, 'error'); } }
function updateBulkDeleteButton() { const button = $('calendar-delete-selected'); if (!button) return; button.disabled = selectedCalendarEvents.size === 0; button.textContent = selectedCalendarEvents.size ? `Elimina selezionati (${selectedCalendarEvents.size})` : 'Elimina selezionati'; }
async function loadCalendarMonth() { const grid = $('calendar-grid'); if (!grid) return; grid.innerHTML = '<p class="calendar-loading">Caricamento calendario...</p>'; try { const headers = await authHeaders(); const statusResponse = await fetch('/api/calendar/status', { headers }); const connection = await readResponse(statusResponse); if (statusResponse.status === 401 || !connection.connected) { document.querySelector('.calendar-shell')?.classList.add('hidden'); grid.replaceChildren(calendarPrompt()); return; } if (!statusResponse.ok) throw new Error(connection.detail || 'Stato Google Calendar non disponibile.'); const start = weekStart(calendarMonth); const end = new Date(start); end.setDate(end.getDate() + 7); const response = await fetch(`/api/calendar/events?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`, { headers }); const events = await readResponse(response); if (!response.ok) throw new Error(events.detail || 'Calendario non disponibile.'); document.querySelector('.calendar-shell')?.classList.remove('hidden'); renderCalendar(events); } catch (error) { grid.innerHTML = `<p class="calendar-loading">${error.message}</p>`; } }
function renderPreview() { const container = $('photo-preview'); container.replaceChildren(); selectedFiles.forEach((file, index) => { const item = document.createElement('div'); item.className = 'preview-item'; const image = document.createElement('img'); image.src = URL.createObjectURL(file); image.alt = file.name; const label = document.createElement('span'); label.textContent = `${String(index + 1).padStart(2, '0')} - ${file.name}`; item.append(image, label); container.appendChild(item); }); }
async function generateCopy(event) { event.preventDefault(); if (!selectedFiles.length) return status('workspace-status', 'Scegli almeno una foto.', 'error'); const button = $('generate-button'); button.disabled = true; status('workspace-status', "L'AI sta analizzando il materiale e preparando la proposta...", 'loading'); const form = new FormData(); selectedFiles.forEach(file => form.append('files', file)); form.append('theme', $('theme').value.trim()); form.append('content_format', $('content-format').value); form.append('title', $('content-title').value.trim() || 'Nuovo momento aziendale'); try { const response = await fetch('/api/generate-copy', { method: 'POST', headers: await authHeaders(), body: form }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Generazione non riuscita.'); renderCopies(data.copies, data.assets, data.draft); $('copy-results').classList.remove('hidden'); loadContent(); status('workspace-status', 'Bozza creata e pronta per approvazione.', 'success'); } catch (error) { status('workspace-status', error.message, 'error'); } finally { button.disabled = false; } }
function renderCopies(copies, assets, draft) { const container = $('copy-container'); container.replaceChildren(); copies.forEach((copy, index) => { const card = document.createElement('article'); card.className = 'copy-card'; const image = document.createElement('img'); image.src = assets[index].url; image.alt = assets[index].filename; const heading = document.createElement('div'); heading.className = 'copy-heading'; const title = document.createElement('h3'); title.textContent = `Output ${String(index + 1).padStart(2, '0')}`; const badge = document.createElement('span'); badge.textContent = draft.status; heading.append(title, badge); const hook = document.createElement('p'); hook.className = 'copy-hook'; hook.textContent = copy.hook; const caption = document.createElement('p'); caption.className = 'copy-caption'; caption.textContent = copy.caption; const tags = document.createElement('p'); tags.className = 'copy-tags'; tags.textContent = copy.hashtags.join(' '); card.append(image, heading, hook, caption, tags); container.appendChild(card); }); }
function renderContentItem(item) { const element = document.createElement('article'); element.className = 'content-row'; element.innerHTML = `<div><span class="content-format"></span><h3></h3><p></p></div><div class="content-actions"><span class="status-pill"></span><button class="btn btn-secondary approve-button">Approva</button><button class="btn btn-primary publish-button">Pubblica</button></div>`; element.querySelector('.content-format').textContent = item.content_format.replaceAll('_', ' '); element.querySelector('h3').textContent = item.title; element.querySelector('p').textContent = new Date(item.created_at).toLocaleString('it-IT'); element.querySelector('.status-pill').textContent = item.status; const approve = element.querySelector('.approve-button'); const publish = element.querySelector('.publish-button'); approve.disabled = item.status !== 'DRAFT'; publish.disabled = item.status !== 'APPROVED'; approve.addEventListener('click', () => updateContent(item.id, 'APPROVED')); publish.addEventListener('click', () => publishContent(item.id)); return element; }
async function loadContent() {
  const container = $('content-library');
  if (!container) return;
  container.replaceChildren();
  try {
    const response = await fetch('/api/content', { headers: await authHeaders() });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Coda non disponibile.');
    if (!Array.isArray(data)) throw new Error('Risposta inattesa dal server.');
    $('draft-count').textContent = data.filter(item => item.status === 'DRAFT').length;
    if (!data.length) {
      container.appendChild(emptyState('Nessun contenuto in coda', 'Carica le foto dal Content Studio per creare la prima bozza.'));
      renderTodayContent([]);
      return;
    }
    data.forEach(item => container.appendChild(renderContentItem(item)));
    renderTodayContent(data);
  } catch (error) {
    container.appendChild(emptyState('Coda non disponibile', error.message));
  }
}
function renderTodayContent(items) {
  const container = $('today-content');
  if (!container) return;
  container.replaceChildren();
  if (!items.length) {
    container.appendChild(emptyState('Ancora nessun contenuto', 'La coda è vuota: crea la prima bozza dal Content Studio.'));
    return;
  }
  items.slice(0, 3).forEach(item => container.appendChild(renderContentItem(item)));
}
async function updateContent(id, nextStatus) { const response = await fetch(`/api/content/${id}`, { method: 'PATCH', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ status: nextStatus }) }); if (!response.ok) { const data = await response.json(); return status('workspace-status', data.detail || 'Aggiornamento non riuscito.', 'error'); } loadContent(); }
async function publishContent(id) { const response = await fetch(`/api/content/${id}/publish`, { method: 'POST', headers: await authHeaders() }); const data = await response.json(); if (!response.ok) return status('workspace-status', data.detail || 'Pubblicazione non riuscita.', 'error'); loadContent(); status('workspace-status', 'Contenuto pubblicato.', 'success'); }
async function syncGoogleCalendar(session) { if (!session?.provider_token) return; const response = await fetch('/api/calendar/connect', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ access_token: session.provider_token, refresh_token: session.provider_refresh_token || '' }) }); if (!response.ok) { const data = await readResponse(response); status('workspace-status', data.detail || 'Google Calendar non collegato.', 'error'); } }
async function connectGoogle() { const { data: { session } } = await supabaseClient.auth.getSession(); if (!session) return status('workspace-status', 'Accedi prima con Google.', 'error'); if (!session.provider_token) return loginWithGoogle(); await syncGoogleCalendar(session); await loadOpportunities('opportunity-list'); status('workspace-status', 'Calendario aggiornato.', 'success'); }
async function scheduleCalendarEvent(event) { event.preventDefault(); const form = event.currentTarget; const days = Array.from(form.querySelectorAll('input[name="schedule-day"]:checked')).map(input => input.value); const start = $('schedule-start').value; const end = $('schedule-end').value; if (!days.length) return status('calendar-schedule-status', 'Seleziona almeno un giorno.', 'error'); if (!start || !end || new Date(end) <= new Date(start)) return status('calendar-schedule-status', 'Controlla data e orario di inizio e fine.', 'error'); const button = form.querySelector('button[type="submit"]'); button.disabled = true; status('calendar-schedule-status', 'Creo la serie di eventi su Google Calendar...', 'loading'); try { const response = await fetch('/api/calendar/events/recurring', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('schedule-title').value.trim(), description: $('schedule-description').value.trim(), start: `${start}:00`, end: `${end}:00`, frequency: form.querySelector('input[name="schedule-frequency"]:checked').value, days, weeks: Number($('schedule-weeks').value) }) }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Pianificazione non riuscita.'); status('calendar-schedule-status', 'Serie di eventi pianificata su Google Calendar.', 'success'); await loadOpportunities('opportunity-list'); } catch (error) { status('calendar-schedule-status', error.message, 'error'); } finally { button.disabled = false; } }
function setupCalendarShell() {
  const planner = $('calendar-planner');
  if (!planner || document.querySelector('.calendar-shell')) return;
  const shell = document.createElement('div');
  shell.className = 'calendar-shell panel';
  const toolbar = document.createElement('div');
  toolbar.className = 'calendar-toolbar';
  const nav = document.createElement('div');
  nav.className = 'calendar-nav';
  const previous = document.createElement('button');
  previous.type = 'button';
  previous.className = 'icon-button';
  previous.id = 'calendar-previous';
  previous.setAttribute('aria-label', 'Settimana precedente');
  previous.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M14 6l-5 6 5 6"/></svg>';
  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'icon-button';
  next.id = 'calendar-next';
  next.setAttribute('aria-label', 'Settimana successiva');
  next.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6l5 6-5 6"/></svg>';
  const today = document.createElement('button');
  today.type = 'button';
  today.className = 'btn btn-secondary';
  today.id = 'calendar-today';
  today.textContent = 'Oggi';
  nav.append(previous, next, today);
  const label = document.createElement('h2');
  label.id = 'calendar-month-label';
  const actions = document.createElement('div');
  actions.className = 'calendar-actions';
  const removeSelected = document.createElement('button');
  removeSelected.type = 'button';
  removeSelected.className = 'btn btn-secondary';
  removeSelected.id = 'calendar-delete-selected';
  removeSelected.disabled = true;
  removeSelected.textContent = 'Elimina selezionati';
  const newSeries = document.createElement('button');
  newSeries.type = 'button';
  newSeries.className = 'btn btn-primary';
  newSeries.id = 'open-calendar-planner';
  newSeries.textContent = '+ Nuova serie';
  actions.append(removeSelected, newSeries);
  toolbar.append(nav, label, actions);
  const grid = document.createElement('div');
  grid.id = 'calendar-grid';
  grid.className = 'calendar-week-grid';
  shell.append(toolbar, grid);
  planner.parentNode.insertBefore(shell, planner);
  planner.classList.add('hidden');
  previous.addEventListener('click', () => { calendarMonth.setDate(calendarMonth.getDate() - 7); loadCalendarMonth(); });
  next.addEventListener('click', () => { calendarMonth.setDate(calendarMonth.getDate() + 7); loadCalendarMonth(); });
  today.addEventListener('click', () => { calendarMonth = new Date(); loadCalendarMonth(); });
  removeSelected.addEventListener('click', deleteSelectedCalendarEvents);
  newSeries.addEventListener('click', () => openCalendarPlanner(new Date()));
}
/* --------------------------------------------------------- piano editoriale */
/*
 * Il reminder nasce dai fatti: se lo stesso giorno della settimana ha riunioni
 * in almeno 3 settimane diverse (vedi backend/services/editorial.py) l'utente
 * ha già un ritmo di lavoro. Mostriamo il punto esclamativo in sidebar e una
 * scheda che offre di trasformare quel ritmo in un piano editoriale.
 */

const PLAN_WEEKDAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
const DISMISS_KEY = 'moment.editorial.dismissed';

function shiftWeekdayCode(code, delta) {
  const index = PLAN_WEEKDAYS.indexOf(code);
  if (index === -1) return code;
  return PLAN_WEEKDAYS[(index + delta + PLAN_WEEKDAYS.length) % PLAN_WEEKDAYS.length];
}

function planWeekdayList(codes) {
  return (codes || []).map(code => WEEKDAY_SHORT[code] || code).join(', ');
}

/* La settimana in corso ha già giorni alle spalle: il piano parte da quella dopo. */
function planStartSuggestion(reference = new Date()) {
  const monday = weekStart(reference);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (monday < today) monday.setDate(monday.getDate() + 7);
  return monday;
}

/*
 * Convertire un suggerimento non deve duplicare le riunioni già in calendario:
 * il piano riparte dalla settimana successiva all'ultima ricorrenza osservata.
 */
function planStartAfterSuggestion(suggestion) {
  const weeks = (suggestion.weeks || []).filter(Boolean).sort();
  const earliest = planStartSuggestion();
  if (!weeks.length) return earliest;
  const start = new Date(`${weeks[weeks.length - 1]}T00:00:00`);
  start.setDate(start.getDate() + 7);
  return start < earliest ? earliest : start;
}

/* I titoli arrivano da Google Calendar: si ripulisce il prefisso delle nostre serie. */
function planTopicFromTitles(titles) {
  const first = (titles || []).find(title => title && title.trim());
  if (!first) return '';
  return first.trim().replace(/^(post|riunione)\s·\s/i, '');
}

function suggestionKey(item) {
  return `${item.weekday}-${item.hour}-${(item.weeks || []).join('_')}`;
}

function dismissedSuggestions() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(DISMISS_KEY) || '[]');
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

function isSuggestionDismissed(item) {
  return dismissedSuggestions().includes(suggestionKey(item));
}

function dismissSuggestion(item) {
  window.localStorage.setItem(DISMISS_KEY, JSON.stringify([...new Set([...dismissedSuggestions(), suggestionKey(item)])]));
  renderEditorialSuggestions();
}

function updateEditorialBadge() {
  const badge = $('nav-badge-editorial');
  if (!badge) return;
  const pending = editorialSuggestions.filter(item => !isSuggestionDismissed(item));
  badge.classList.toggle('hidden', !pending.length);
  if (pending.length) badge.title = pending[0].message;
}

function renderSuggestionCard(item) {
  const card = document.createElement('article');
  card.className = 'suggestion-card';

  const mark = document.createElement('span');
  mark.className = 'suggestion-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = '!';

  const body = document.createElement('div');
  body.className = 'suggestion-body';

  const step = document.createElement('span');
  step.className = 'step';
  step.textContent = 'Promemoria ritmo ricorrente';
  const heading = document.createElement('h3');
  heading.textContent = `${item.weekday_label} alle ${String(item.hour).padStart(2, '0')}:00 ti ripeti`;
  const message = document.createElement('p');
  message.textContent = item.message;
  body.append(step, heading, message);

  // Il piano proposto: la riunione resta il giorno osservato, il post esce il giorno dopo.
  const postDay = shiftWeekdayCode(item.weekday, 1);
  const start = planStartAfterSuggestion(item);
  const meta = document.createElement('div');
  meta.className = 'suggestion-meta';
  [
    `${item.occurrences} ricorrenze in ${item.weeks.length} settimane`,
    `riunioni ${WEEKDAY_SHORT[item.weekday] || item.weekday}`,
    `post ${WEEKDAY_SHORT[postDay] || postDay}`,
    `dal ${start.toLocaleDateString('it-IT', { day: 'numeric', month: 'short' })}`,
  ].forEach(text => {
    const pill = document.createElement('span');
    pill.className = 'status-pill';
    pill.textContent = text;
    meta.appendChild(pill);
  });

  const note = document.createElement('p');
  note.className = 'suggestion-note';
  note.textContent = 'Le riunioni già in calendario non vengono toccate: il piano crea le serie successive.';
  body.append(meta, note);

  if (item.titles?.length) {
    const list = document.createElement('ul');
    list.className = 'suggestion-titles';
    item.titles.forEach(title => {
      const entry = document.createElement('li');
      entry.textContent = title;
      list.appendChild(entry);
    });
    body.appendChild(list);
  }

  const actions = document.createElement('div');
  actions.className = 'suggestion-actions';
  const accept = document.createElement('button');
  accept.type = 'button';
  accept.className = 'btn btn-primary';
  accept.textContent = 'Crea il piano editoriale';
  accept.addEventListener('click', () => openEditorialPanel({ suggestion: item, postDay }));
  const later = document.createElement('button');
  later.type = 'button';
  later.className = 'text-button';
  later.textContent = 'Non ora';
  later.addEventListener('click', () => dismissSuggestion(item));
  actions.append(accept, later);
  body.appendChild(actions);

  card.append(mark, body);
  return card;
}

function renderEditorialSuggestions() {
  updateEditorialBadge();
  const container = $('editorial-suggestions');
  if (!container) return;
  container.replaceChildren();
  const pending = editorialSuggestions.filter(item => !isSuggestionDismissed(item));
  if (!pending.length) {
    container.classList.add('hidden');
    return;
  }
  pending.forEach(item => container.appendChild(renderSuggestionCard(item)));
  container.classList.remove('hidden');
}

/*
 * Un calendario non leggibile non è un errore da urlare all'utente: il reminder
 * semplicemente non compare. La vista Opportunities mostra già il suo stato.
 */
async function loadEditorialSuggestions() {
  try {
    const response = await fetch('/api/editorial/suggestions', { headers: await authHeaders() });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Suggerimenti non disponibili.');
    editorialSuggestions = Array.isArray(data) ? data : [];
  } catch {
    editorialSuggestions = [];
  }
  renderEditorialSuggestions();
}

function openEditorialPanel({ suggestion, postDay } = {}) {
  const panel = $('editorial-plan-panel');
  if (!panel) return;
  panel.classList.remove('hidden');
  if (!$('plan-start').value) $('plan-start').value = calendarDateKey(planStartSuggestion());
  if (suggestion) {
    $('plan-topic').value = planTopicFromTitles(suggestion.titles) || '';
    $('plan-start').value = calendarDateKey(planStartAfterSuggestion(suggestion));
    document.querySelectorAll('input[name="plan-post-day"]').forEach(input => { input.checked = input.value === postDay; });
    $('plan-meeting-time').value = `${String(suggestion.hour).padStart(2, '0')}:00`;
  }
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function submitEditorialPlan(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const days = [...form.querySelectorAll('input[name="plan-post-day"]:checked')].map(input => input.value);
  if (!days.length) return status('editorial-plan-status', 'Seleziona almeno un giorno per i post.', 'error');
  const payload = {
    topic: $('plan-topic').value.trim(), content_format: $('plan-format').value,
    start_date: $('plan-start').value, weeks: Number($('plan-weeks').value),
    frequency: $('plan-frequency').value, post_days: days,
    post_time: $('plan-post-time').value, post_duration_minutes: Number($('plan-post-duration').value),
    meeting_shift_days: Number($('plan-meeting-shift').value), meeting_time: $('plan-meeting-time').value,
    meeting_duration_minutes: Number($('plan-meeting-duration').value), notes: $('plan-notes').value.trim(),
  };
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  status('editorial-plan-status', 'Creo le serie di post e riunioni su Google Calendar…', 'loading');
  try {
    const response = await fetch('/api/editorial/plans', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Creazione del piano non riuscita.');
    form.reset();
    $('plan-start').value = calendarDateKey(planStartSuggestion());
    status('editorial-plan-status', `Piano creato: ${data.posts_planned} post e ${data.meetings_planned} riunioni.`, 'success');
    await Promise.all([loadEditorialPlans(), loadCalendarMonth(), loadEditorialSuggestions()]);
  } catch (error) {
    status('editorial-plan-status', error.message, 'error');
  } finally { button.disabled = false; }
}

function renderEditorialPlan(plan) {
  const card = document.createElement('article');
  card.className = 'plan-row';
  const details = document.createElement('div');
  const title = document.createElement('h3'); title.textContent = plan.topic;
  const summary = document.createElement('p');
  summary.textContent = `${plan.posts_planned} post · ${plan.meetings_planned} riunioni · ${plan.weeks} settimane · ${planWeekdayList(plan.post_days)}`;
  details.append(title, summary);
  const actions = document.createElement('div'); actions.className = 'plan-actions';
  const link = document.createElement('a'); link.className = 'text-button'; link.textContent = 'Apri Calendar';
  link.href = plan.post_event_link || plan.meeting_event_link || '#'; link.target = '_blank'; link.rel = 'noopener noreferrer';
  if (!plan.post_event_link && !plan.meeting_event_link) link.hidden = true;
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button danger-text'; remove.textContent = 'Elimina';
  remove.addEventListener('click', () => deleteEditorialPlan(plan));
  actions.append(link, remove); card.append(details, actions); return card;
}

async function loadEditorialPlans() {
  const container = $('editorial-plan-list'); if (!container) return;
  try {
    const response = await fetch('/api/editorial/plans', { headers: await authHeaders() });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Piani non disponibili.');
    container.replaceChildren();
    if (!data.length) container.appendChild(emptyState('Nessun piano editoriale', 'Crea un piano per programmare post e riunioni ricorrenti.'));
    else data.forEach(plan => container.appendChild(renderEditorialPlan(plan)));
  } catch (error) { container.replaceChildren(emptyState('Piani non disponibili', error.message)); }
}

async function deleteEditorialPlan(plan) {
  if (!window.confirm(`Eliminare il piano “${plan.topic}” e le relative serie dal calendario?`)) return;
  try {
    const response = await fetch(`/api/editorial/plans/${encodeURIComponent(plan.id)}`, { method: 'DELETE', headers: await authHeaders() });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Eliminazione non riuscita.');
    status('editorial-plan-status', 'Piano e serie eliminate.', 'success');
    await Promise.all([loadEditorialPlans(), loadCalendarMonth(), loadEditorialSuggestions()]);
  } catch (error) { status('editorial-plan-status', error.message, 'error'); }
}

function renderPublicationNote() {
  const note = $('publication-note'); if (!note) return;
  // Il processore manuale è solo per lo sviluppo locale; in produzione lo chiama il cron.
  const runButton = $('run-publications');
  if (runButton) runButton.classList.toggle('hidden', Boolean(appConfig.supabase_url));
  note.textContent = appConfig.scheduler_configured
    ? 'La coda verrà elaborata automaticamente all’orario programmato.'
    : 'La coda è salvata; configura CRON_SECRET e uno scheduler per pubblicare automaticamente.';
}

async function loadPublications() {
  const container = $('publication-list'); if (!container) return;
  try {
    const response = await fetch('/api/publications', { headers: await authHeaders() });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Coda di pubblicazione non disponibile.');
    container.replaceChildren();
    if (!data.length) { container.appendChild(emptyState('La coda è vuota', 'I post programmati compariranno qui.')); return; }
    data.forEach(item => {
      const row = document.createElement('article'); row.className = 'publication-row';
      const main = document.createElement('div'); main.className = 'publication-main';
      const caption = document.createElement('strong'); caption.textContent = item.caption || item.calendar_event_title || 'Post senza didascalia';
      const date = document.createElement('span'); date.textContent = new Date(item.scheduled_at).toLocaleString('it-IT', { dateStyle: 'medium', timeStyle: 'short' });
      main.append(caption, date);
      const state = document.createElement('span'); state.className = `status-pill publication-${item.status.toLowerCase()}`; state.textContent = PUBLICATION_LABELS[item.status] || item.status;
      row.append(main, state);
      if (item.status === 'SCHEDULED') {
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'text-button danger-text'; cancel.textContent = 'Annulla';
        cancel.addEventListener('click', () => cancelPublication(item.id)); row.appendChild(cancel);
      }
      if (item.error) { const error = document.createElement('p'); error.className = 'publication-error'; error.textContent = item.error; row.appendChild(error); }
      container.appendChild(row);
    });
  } catch (error) { container.replaceChildren(emptyState('Coda non disponibile', error.message)); }
}

async function schedulePublication(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const files = Array.from($('publication-input').files || []);
  if (!files.length && !pendingAssetIds.length) return status('publication-status', 'Carica almeno un’immagine.', 'error');
  const when = $('publication-date').value;
  if (!when || Number.isNaN(new Date(when).getTime())) return status('publication-status', 'Indica data e ora valide.', 'error');
  const body = new FormData();
  files.forEach(file => body.append('files', file));
  if (pendingAssetIds.length) body.append('asset_ids', pendingAssetIds.join(','));
  body.append('caption', $('publication-caption').value.trim());
  body.append('scheduled_at', new Date(when).toISOString());
  if (linkedCalendarEvent) { body.append('calendar_event_id', linkedCalendarEvent.id); body.append('calendar_event_title', linkedCalendarEvent.title); }
  const button = form.querySelector('[type="submit"]'); button.disabled = true;
  status('publication-status', 'Salvo il post in coda…', 'loading');
  try {
    const response = await fetch('/api/publications', { method: 'POST', headers: await authHeaders(), body });
    const data = await readResponse(response);
    if (!response.ok) throw new Error(data.detail || 'Programmazione non riuscita.');
    form.reset(); pendingAssetIds = []; linkedCalendarEvent = null;
    $('publication-preview').replaceChildren(); $('publication-event-group').classList.add('hidden');
    $('publication-assets-group').classList.add('hidden');
    status('publication-status', 'Post aggiunto alla coda di pubblicazione.', 'success'); await loadPublications();
  } catch (error) { status('publication-status', error.message, 'error'); }
  finally { button.disabled = false; }
}

async function cancelPublication(id) {
  try {
    const response = await fetch(`/api/publications/${encodeURIComponent(id)}`, { method: 'DELETE', headers: await authHeaders() });
    const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Annullamento non riuscito.');
    await loadPublications();
  } catch (error) { status('publication-status', error.message, 'error'); }
}

async function runPublications() {
  const button = $('run-publications'); button.disabled = true;
  try {
    const response = await fetch('/api/publications/run', { method: 'POST', headers: await authHeaders() });
    const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Elaborazione coda non riuscita.');
    status('publication-status', `${data.published} pubblicati, ${data.failed} non riusciti.`, data.failed ? 'error' : 'success');
    await loadPublications();
  } catch (error) { status('publication-status', error.message, 'error'); }
  finally { button.disabled = false; }
}


async function connectMeta() { if (!supabaseClient) return status('workspace-status', 'Accedi prima con Google.', 'error'); const { data: { session } } = await supabaseClient.auth.getSession(); if (!session) return status('workspace-status', 'Accedi prima con Google.', 'error'); const response = await fetch('/api/auth/meta/start', { headers: { Authorization: `Bearer ${session.access_token}` } }); const data = await response.json(); if (!response.ok) return status('workspace-status', data.detail || 'Meta non configurato.', 'error'); window.location.href = data.url; }
async function authHeaders() {
  if (!supabaseClient) return {};
  const { data: { session } } = await supabaseClient.auth.getSession();
  if (!session) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}
async function loadAnalytics() {
  try {
    const response = await fetch('/api/analytics', { headers: await authHeaders() });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Analytics non disponibili.');
    $('today-reach').textContent = Number(data.total_reach).toLocaleString('it-IT');
    $('kpi-top-er').textContent = `${data.posts[0]?.engagement_rate || 0}%`;
    $('kpi-avg-er').textContent = `${data.avg_engagement_rate}%`;
    $('kpi-avg-save').textContent = `${data.avg_save_rate}%`;
    $('kpi-reach').textContent = Number(data.total_reach).toLocaleString('it-IT');
    renderCharts(data.posts);
    renderPosts(data.posts);
  } catch (error) {
    $('connection-status').textContent = error.message;
    $('posts-container')?.replaceChildren(emptyState('Analytics non disponibili', error.message));
  }
}
function tuneCharts() {
  if (!window.Chart) return;
  window.Chart.defaults.font.family = "'Manrope', system-ui, -apple-system, 'Segoe UI', sans-serif";
  window.Chart.defaults.font.size = 11;
  window.Chart.defaults.color = '#5a6673';
  window.Chart.defaults.borderColor = '#e2e6eb';
}

function renderCharts(posts) { const labels = posts.map(post => `#${post.id}`); if (erChartInstance) erChartInstance.destroy(); erChartInstance = new Chart($('chart-er').getContext('2d'), { type: 'bar', data: { labels, datasets: [{ data: posts.map(post => post.engagement_rate), backgroundColor: '#2563eb', borderRadius: 3 }] }, options: { responsive: true, plugins: { legend: { display: false } } } }); if (matrixChartInstance) matrixChartInstance.destroy(); matrixChartInstance = new Chart($('chart-matrix').getContext('2d'), { type: 'scatter', data: { datasets: [{ data: posts.map(post => ({ x: post.save_rate, y: post.conversation_rate })), backgroundColor: '#17795e', pointRadius: 6 }] }, options: { responsive: true, plugins: { legend: { display: false } } } }); }
function formatMediaType(type) {
  return { IMAGE: 'Post immagine', CAROUSEL_ALBUM: 'Carosello', VIDEO: 'Reel' }[type] || type;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' });
}

function renderPosts(posts) { const container = $('posts-container'); container.replaceChildren(); if (!posts.length) { container.appendChild(emptyState('Nessun post analizzato', 'Collega un account Instagram, oppure attiva la modalità demo, per popolare questa vista.')); return; } posts.forEach(post => { const item = document.createElement('article'); item.className = 'post-item'; const image = document.createElement('img'); image.className = 'post-img'; image.src = post.local_files?.[0] || post.media_url; image.alt = 'Contenuto pubblicato'; const details = document.createElement('div'); details.className = 'post-details'; const title = document.createElement('h3'); title.textContent = `${formatMediaType(post.media_type)} · ${formatDate(post.date)}`; const caption = document.createElement('p'); caption.className = 'post-caption'; caption.textContent = post.caption; details.append(title, caption); item.append(image, details); container.appendChild(item); }); }
async function loadHome() { await Promise.all([loadOpportunities(), loadContent(), loadAnalytics()]); }
document.querySelectorAll('.nav-btn, [data-view-target]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view || button.dataset.viewTarget)));
$('photo-input').addEventListener('change', event => { selectedFiles = Array.from(event.target.files); renderPreview(); });
$('copy-form').addEventListener('submit', generateCopy);
$('google-connect').addEventListener('click', connectGoogle);
$('calendar-schedule-form').addEventListener('submit', scheduleCalendarEvent);
$('close-calendar-planner')?.addEventListener('click', () => $('calendar-planner').classList.add('hidden'));
$('open-editorial-panel')?.addEventListener('click', () => openEditorialPanel());
$('close-editorial-panel')?.addEventListener('click', () => $('editorial-plan-panel').classList.add('hidden'));
$('editorial-plan-form')?.addEventListener('submit', submitEditorialPlan);
$('publication-form')?.addEventListener('submit', schedulePublication);
$('publication-input')?.addEventListener('change', event => {
  const container = $('publication-preview'); container.replaceChildren();
  Array.from(event.target.files || []).forEach(file => {
    const image = document.createElement('img'); image.className = 'publication-preview-image';
    image.src = URL.createObjectURL(file); image.alt = file.name; container.appendChild(image);
  });
});
$('clear-publication-event')?.addEventListener('click', () => { linkedCalendarEvent = null; $('publication-event-group').classList.add('hidden'); });
$('clear-publication-assets')?.addEventListener('click', () => { pendingAssetIds = []; $('publication-assets-group').classList.add('hidden'); });
$('run-publications')?.addEventListener('click', runPublications);
$('refresh-publications')?.addEventListener('click', loadPublications);
$('refresh-content').addEventListener('click', loadContent);
$('refresh-analytics').addEventListener('click', loadAnalytics);
$('login-google').addEventListener('click', loginWithGoogle);
$('meta-connect').addEventListener('click', connectMeta);
$('logout').addEventListener('click', logout);
window.addEventListener('DOMContentLoaded', async () => { setupCalendarShell(); initSidebar(); tuneCharts(); renderTodayDate(); if ($('plan-start')) $('plan-start').value = calendarDateKey(planStartSuggestion()); await initializeAuth(); const opportunities = $('view-opportunities'); if (opportunities?.classList.contains('active')) { loadCalendarMonth(); loadEditorialSuggestions(); loadEditorialPlans(); } loadHome(); });

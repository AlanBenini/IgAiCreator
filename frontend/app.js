let erChartInstance = null;
let matrixChartInstance = null;
let selectedFiles = [];
let supabaseClient = null;
let calendarMonth = new Date();
let selectedCalendarEvents = new Set();
const $ = id => document.getElementById(id);

async function initializeAuth() {
  const response = await fetch('/api/config');
  const config = await response.json();
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
  if (view === 'opportunities') { loadOpportunities('opportunity-list'); loadCalendarMonth(); }
  if (view === 'content') loadContent();
  if (view === 'performance') loadAnalytics();
}
function status(id, message, type = '') { const element = $(id); element.textContent = message; element.className = `status-msg ${type}`; }
function priorityClass(priority) { return priority === 'ALTA' ? 'priority-high' : priority === 'MEDIA' ? 'priority-medium' : 'priority-low'; }
function renderOpportunity(item) { const element = document.createElement('article'); element.className = 'opportunity-card'; element.innerHTML = `<div class="opportunity-top"><span class="priority ${priorityClass(item.priority)}">${item.priority}</span><span class="opportunity-date">${item.start.slice(0, 16).replace('T', ' ')}</span></div><h3></h3><p></p><div class="brief-list"></div>`; element.querySelector('h3').textContent = item.title; element.querySelector('p').textContent = item.reason; item.capture_brief.forEach(line => { const brief = document.createElement('span'); brief.textContent = line; element.querySelector('.brief-list').appendChild(brief); }); return element; }
async function readResponse(response) { const raw = await response.text(); try { return raw ? JSON.parse(raw) : {}; } catch { return { detail: raw || `Errore HTTP ${response.status}` }; } }
async function loadOpportunities(targetId = 'today-opportunities') { const container = $(targetId); container.replaceChildren(); try { const headers = await authHeaders(); const connectionResponse = await fetch('/api/calendar/status', { headers }); const connection = await readResponse(connectionResponse); if (!connectionResponse.ok) throw new Error(connection.detail || 'Stato Google Calendar non disponibile.'); if (!connection.connected) { container.innerHTML = '<p class="empty-state">Collega Google Calendar per attivare il Content Radar.</p>'; return; } const response = await fetch('/api/opportunities', { headers }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Calendario non disponibile.'); if (!data.length) { container.innerHTML = '<p class="empty-state">Google Calendar è collegato, ma non ci sono eventi nelle prossime 4 settimane.</p>'; return; } data.forEach(item => container.appendChild(renderOpportunity(item))); } catch (error) { container.innerHTML = `<p class="empty-state">${error.message}</p>`; } }
function calendarDateKey(date) { return date.toISOString().slice(0, 10); }
function localDateTimeValue(date, hour) { const copy = new Date(date); copy.setHours(hour, 0, 0, 0); const offset = copy.getTimezoneOffset() * 60000; return new Date(copy.getTime() - offset).toISOString().slice(0, 16); }
function openCalendarPlanner(date) { const planner = $('calendar-planner'); if (!planner) return; planner.classList.remove('hidden'); $('schedule-start').value = localDateTimeValue(date, 9); $('schedule-end').value = localDateTimeValue(date, 10); const weekday = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'][date.getDay()]; document.querySelectorAll('input[name="schedule-day"]').forEach(input => { input.checked = input.value === weekday; }); planner.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function weekStart(date) { const start = new Date(date); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); return start; }
function formatEventInput(raw) { if (!raw) return ''; if (raw.length === 10) return `${raw}T09:00`; const date = new Date(raw); const offset = date.getTimezoneOffset() * 60000; return new Date(date.getTime() - offset).toISOString().slice(0, 16); }
function openEventEditor(calendarEvent) { let editor = $('calendar-event-editor'); if (!editor) { editor = document.createElement('section'); editor.id = 'calendar-event-editor'; editor.className = 'calendar-event-editor panel'; $('calendar-grid').parentNode.after(editor); } editor.innerHTML = '<div class="panel-heading"><div><span class="step">EVENTO</span><h2>Modifica evento</h2></div><button class="icon-button" type="button" id="close-event-editor" aria-label="Chiudi">&times;</button></div><form id="calendar-edit-form" class="event-edit-form"><label>Titolo<input id="edit-event-title" type="text" required></label><label>Descrizione<textarea id="edit-event-description" rows="3"></textarea></label><div class="schedule-time"><label>Inizio<input id="edit-event-start" type="datetime-local" required></label><label>Fine<input id="edit-event-end" type="datetime-local" required></label></div><div class="event-editor-actions"><button class="btn btn-danger" id="delete-event-button" type="button">Elimina</button><button class="btn btn-primary" type="submit">Salva modifiche</button></div><div id="event-editor-status" class="status-msg hidden"></div></form>'; $('edit-event-title').value = calendarEvent.summary || ''; $('edit-event-description').value = calendarEvent.description || ''; $('edit-event-start').value = formatEventInput(calendarEvent.start?.dateTime || calendarEvent.start?.date); $('edit-event-end').value = formatEventInput(calendarEvent.end?.dateTime || calendarEvent.end?.date); $('close-event-editor').onclick = () => editor.remove(); $('delete-event-button').onclick = () => { if (window.confirm(`Eliminare '${calendarEvent.summary || 'Evento'}'?`)) deleteCalendarEvent(calendarEvent.id).then(() => editor.remove()); }; $('calendar-edit-form').onsubmit = async event => { event.preventDefault(); const message = $('event-editor-status'); try { const response = await fetch(`/api/calendar/events/${encodeURIComponent(calendarEvent.id)}`, { method: 'PATCH', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('edit-event-title').value.trim(), description: $('edit-event-description').value.trim(), start: `${$('edit-event-start').value}:00`, end: `${$('edit-event-end').value}:00` }) }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Modifica non riuscita.'); message.textContent = 'Evento aggiornato.'; message.className = 'status-msg success'; await loadCalendarMonth(); await loadOpportunities('opportunity-list'); } catch (error) { message.textContent = error.message; message.className = 'status-msg error'; } }; editor.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
function renderCalendar(events = []) { const grid = $('calendar-grid'); if (!grid) return; const start = weekStart(calendarMonth); const end = new Date(start); end.setDate(end.getDate() + 7); $('calendar-month-label').textContent = `${start.toLocaleDateString('it-IT', { day: 'numeric', month: 'short' })} - ${new Date(end - 86400000).toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })}`; grid.replaceChildren(); const header = document.createElement('div'); header.className = 'calendar-week-header'; header.innerHTML = '<span></span>'; for (let index = 0; index < 7; index += 1) { const date = new Date(start); date.setDate(start.getDate() + index); const day = document.createElement('span'); day.className = calendarDateKey(date) === calendarDateKey(new Date()) ? 'is-today' : ''; day.textContent = date.toLocaleDateString('it-IT', { weekday: 'short', day: 'numeric' }); header.appendChild(day); } const body = document.createElement('div'); body.className = 'calendar-week-body'; const timeAxis = document.createElement('div'); timeAxis.className = 'calendar-time-axis'; for (let hour = 0; hour < 24; hour += 1) { const label = document.createElement('span'); label.textContent = `${String(hour).padStart(2, '0')}:00`; timeAxis.appendChild(label); } body.appendChild(timeAxis); for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) { const column = document.createElement('div'); column.className = 'calendar-day-column'; column.addEventListener('click', event => { if (event.target === column) { const rect = column.getBoundingClientRect(); const date = new Date(start); date.setDate(start.getDate() + dayIndex); date.setHours(Math.min(23, Math.floor((event.clientY - rect.top) / 60)), 0, 0, 0); openCalendarPlanner(date); } }); body.appendChild(column); } events.forEach(calendarEvent => { const rawStart = calendarEvent.start?.dateTime || calendarEvent.start?.date; if (!rawStart || !calendarEvent.start?.dateTime) return; const eventDate = new Date(rawStart); const dayIndex = Math.floor((new Date(eventDate.getFullYear(), eventDate.getMonth(), eventDate.getDate()) - new Date(start.getFullYear(), start.getMonth(), start.getDate())) / 86400000); if (dayIndex < 0 || dayIndex > 6) return; const rawEnd = calendarEvent.end?.dateTime || rawStart; const duration = Math.max(30, (new Date(rawEnd) - eventDate) / 60000); const eventCard = document.createElement('button'); eventCard.type = 'button'; eventCard.className = 'calendar-timed-event'; eventCard.style.top = `${eventDate.getHours() * 60 + eventDate.getMinutes()}px`; eventCard.style.height = `${Math.min(1380, duration)}px`; eventCard.textContent = calendarEvent.summary || 'Evento'; eventCard.title = 'Modifica evento'; eventCard.onclick = event => { event.stopPropagation(); openEventEditor(calendarEvent); }; body.children[dayIndex + 1].appendChild(eventCard); }); grid.append(header, body); updateBulkDeleteButton(); }
async function deleteCalendarEvent(eventId) { try { const response = await fetch(`/api/calendar/events/${encodeURIComponent(eventId)}`, { method: 'DELETE', headers: await authHeaders() }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Eliminazione non riuscita.'); await loadCalendarMonth(); await loadOpportunities('opportunity-list'); } catch (error) { status('workspace-status', error.message, 'error'); } }
function renderCalendar(events = []) {
  const grid = $('calendar-grid');
  if (!grid) return;
  const start = weekStart(calendarMonth);
  const end = new Date(start);
  end.setDate(end.getDate() + 7);
  $('calendar-month-label').textContent = `${start.toLocaleDateString('it-IT', { day: 'numeric', month: 'short' })} - ${new Date(end - 86400000).toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' })}`;
  grid.replaceChildren();
  const header = document.createElement('div');
  header.className = 'calendar-week-header';
  header.innerHTML = '<span></span>';
  for (let index = 0; index < 7; index += 1) {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    const day = document.createElement('span');
    day.className = calendarDateKey(date) === calendarDateKey(new Date()) ? 'is-today' : '';
    day.textContent = date.toLocaleDateString('it-IT', { weekday: 'short', day: 'numeric' });
    header.appendChild(day);
  }
  const body = document.createElement('div');
  body.className = 'calendar-week-body';
  const timeAxis = document.createElement('div');
  timeAxis.className = 'calendar-time-axis';
  for (let hour = 0; hour < 24; hour += 1) {
    const label = document.createElement('span');
    label.textContent = `${String(hour).padStart(2, '0')}:00`;
    timeAxis.appendChild(label);
  }
  body.appendChild(timeAxis);
  for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
    const column = document.createElement('div');
    column.className = 'calendar-day-column';
    column.addEventListener('click', event => {
      if (event.target !== column) return;
      const rect = column.getBoundingClientRect();
      const date = new Date(start);
      date.setDate(start.getDate() + dayIndex);
      date.setHours(Math.min(23, Math.floor((event.clientY - rect.top) / 60)), 0, 0, 0);
      openCalendarPlanner(date);
    });
    body.appendChild(column);
  }
  events.forEach(calendarEvent => {
    if (!calendarEvent.start?.dateTime) return;
    const eventDate = new Date(calendarEvent.start.dateTime);
    const dayIndex = Math.floor((new Date(eventDate.getFullYear(), eventDate.getMonth(), eventDate.getDate()) - new Date(start.getFullYear(), start.getMonth(), start.getDate())) / 86400000);
    if (dayIndex < 0 || dayIndex > 6) return;
    const eventEnd = new Date(calendarEvent.end?.dateTime || calendarEvent.start.dateTime);
    const duration = Math.max(30, (eventEnd - eventDate) / 60000);
    const row = document.createElement('div');
    row.className = 'calendar-timed-event-row';
    row.style.top = `${eventDate.getHours() * 60 + eventDate.getMinutes()}px`;
    row.style.height = `${Math.min(1380, duration)}px`;
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = selectedCalendarEvents.has(calendarEvent.id);
    checkbox.setAttribute('aria-label', `Seleziona ${calendarEvent.summary || 'evento'}`);
    checkbox.addEventListener('click', event => event.stopPropagation());
    checkbox.addEventListener('change', event => {
      if (event.target.checked) selectedCalendarEvents.add(calendarEvent.id);
      else selectedCalendarEvents.delete(calendarEvent.id);
      updateBulkDeleteButton();
    });
    const eventCard = document.createElement('button');
    eventCard.type = 'button';
    eventCard.className = 'calendar-timed-event';
    eventCard.textContent = calendarEvent.summary || 'Evento';
    eventCard.title = 'Modifica evento';
    eventCard.addEventListener('click', event => { event.stopPropagation(); openEventEditor(calendarEvent); });
    row.append(checkbox, eventCard);
    body.children[dayIndex + 1].appendChild(row);
  });
  grid.append(header, body);
  updateBulkDeleteButton();
}
async function deleteSelectedCalendarEvents() { const eventIds = [...selectedCalendarEvents]; if (!eventIds.length) return; if (!window.confirm(`Eliminare ${eventIds.length} eventi selezionati?`)) return; try { const response = await fetch('/api/calendar/events/bulk-delete', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ event_ids: eventIds }) }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Eliminazione multipla non riuscita.'); selectedCalendarEvents.clear(); await loadCalendarMonth(); await loadOpportunities('opportunity-list'); status('workspace-status', `${data.count} eventi eliminati.`, 'success'); } catch (error) { status('workspace-status', error.message, 'error'); } }
function updateBulkDeleteButton() { const button = $('calendar-delete-selected'); if (!button) return; button.disabled = selectedCalendarEvents.size === 0; button.textContent = selectedCalendarEvents.size ? `Elimina selezionati (${selectedCalendarEvents.size})` : 'Elimina selezionati'; }
async function loadCalendarMonth() { const grid = $('calendar-grid'); if (!grid) return; grid.innerHTML = '<p class="calendar-loading">Caricamento calendario...</p>'; try { const headers = await authHeaders(); const statusResponse = await fetch('/api/calendar/status', { headers }); const connection = await readResponse(statusResponse); if (!statusResponse.ok) throw new Error(connection.detail || 'Stato Google Calendar non disponibile.'); if (!connection.connected) { grid.innerHTML = '<p class="calendar-loading">Collega Google Calendar per visualizzare il calendario.</p>'; return; } const start = weekStart(calendarMonth); const end = new Date(start); end.setDate(end.getDate() + 7); const response = await fetch(`/api/calendar/events?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`, { headers }); const events = await readResponse(response); if (!response.ok) throw new Error(events.detail || 'Calendario non disponibile.'); renderCalendar(events); } catch (error) { grid.innerHTML = `<p class="calendar-loading">${error.message}</p>`; } }
function renderPreview() { const container = $('photo-preview'); container.replaceChildren(); selectedFiles.forEach((file, index) => { const item = document.createElement('div'); item.className = 'preview-item'; const image = document.createElement('img'); image.src = URL.createObjectURL(file); image.alt = file.name; const label = document.createElement('span'); label.textContent = `${String(index + 1).padStart(2, '0')} - ${file.name}`; item.append(image, label); container.appendChild(item); }); }
async function generateCopy(event) { event.preventDefault(); if (!selectedFiles.length) return status('workspace-status', 'Scegli almeno una foto.', 'error'); const button = $('generate-button'); button.disabled = true; status('workspace-status', "L'AI sta analizzando il materiale e preparando la proposta...", 'loading'); const form = new FormData(); selectedFiles.forEach(file => form.append('files', file)); form.append('theme', $('theme').value.trim()); form.append('content_format', $('content-format').value); form.append('title', $('content-title').value.trim() || 'Nuovo momento aziendale'); try { const response = await fetch('/api/generate-copy', { method: 'POST', body: form }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Generazione non riuscita.'); renderCopies(data.copies, data.assets, data.draft); $('copy-results').classList.remove('hidden'); loadContent(); status('workspace-status', 'Bozza creata e pronta per approvazione.', 'success'); } catch (error) { status('workspace-status', error.message, 'error'); } finally { button.disabled = false; } }
function renderCopies(copies, assets, draft) { const container = $('copy-container'); container.replaceChildren(); copies.forEach((copy, index) => { const card = document.createElement('article'); card.className = 'copy-card'; const image = document.createElement('img'); image.src = assets[index].url; image.alt = assets[index].filename; const heading = document.createElement('div'); heading.className = 'copy-heading'; const title = document.createElement('h3'); title.textContent = `Output ${String(index + 1).padStart(2, '0')}`; const badge = document.createElement('span'); badge.textContent = draft.status; heading.append(title, badge); const hook = document.createElement('p'); hook.className = 'copy-hook'; hook.textContent = copy.hook; const caption = document.createElement('p'); caption.className = 'copy-caption'; caption.textContent = copy.caption; const tags = document.createElement('p'); tags.className = 'copy-tags'; tags.textContent = copy.hashtags.join(' '); card.append(image, heading, hook, caption, tags); container.appendChild(card); }); }
function renderContentItem(item) { const element = document.createElement('article'); element.className = 'content-row'; element.innerHTML = `<div><span class="content-format"></span><h3></h3><p></p></div><div class="content-actions"><span class="status-pill"></span><button class="btn btn-secondary approve-button">Approva</button><button class="btn btn-primary publish-button">Pubblica</button></div>`; element.querySelector('.content-format').textContent = item.content_format.replaceAll('_', ' '); element.querySelector('h3').textContent = item.title; element.querySelector('p').textContent = new Date(item.created_at).toLocaleString('it-IT'); element.querySelector('.status-pill').textContent = item.status; const approve = element.querySelector('.approve-button'); const publish = element.querySelector('.publish-button'); approve.disabled = item.status !== 'DRAFT'; publish.disabled = item.status !== 'APPROVED'; approve.addEventListener('click', () => updateContent(item.id, 'APPROVED')); publish.addEventListener('click', () => publishContent(item.id)); return element; }
async function loadContent() { const container = $('content-library'); if (!container) return; container.replaceChildren(); try { const response = await fetch('/api/content'); const data = await response.json(); $('draft-count').textContent = data.filter(item => item.status === 'DRAFT').length; if (!data.length) { container.innerHTML = '<p class="empty-state">Nessuna bozza. Carica un momento dal Content Studio.</p>'; return; } data.forEach(item => container.appendChild(renderContentItem(item))); renderTodayContent(data); } catch (error) { container.innerHTML = `<p class="empty-state">${error.message}</p>`; } }
function renderTodayContent(items) { const container = $('today-content'); if (!container) return; container.replaceChildren(); items.slice(0, 3).forEach(item => container.appendChild(renderContentItem(item))); if (!items.length) container.innerHTML = '<p class="empty-state">Ancora nessun contenuto creato.</p>'; }
async function updateContent(id, nextStatus) { const response = await fetch(`/api/content/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: nextStatus }) }); if (!response.ok) { const data = await response.json(); return status('workspace-status', data.detail || 'Aggiornamento non riuscito.', 'error'); } loadContent(); }
async function publishContent(id) { const response = await fetch(`/api/content/${id}/publish`, { method: 'POST', headers: await authHeaders() }); const data = await response.json(); if (!response.ok) return status('workspace-status', data.detail || 'Pubblicazione non riuscita.', 'error'); loadContent(); status('workspace-status', 'Contenuto pubblicato.', 'success'); }
async function syncGoogleCalendar(session) { if (!session?.provider_token) return; const response = await fetch('/api/calendar/connect', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ access_token: session.provider_token, refresh_token: session.provider_refresh_token || '' }) }); if (!response.ok) { const data = await readResponse(response); status('workspace-status', data.detail || 'Google Calendar non collegato.', 'error'); } }
async function connectGoogle() { const { data: { session } } = await supabaseClient.auth.getSession(); if (!session) return status('workspace-status', 'Accedi prima con Google.', 'error'); if (!session.provider_token) return loginWithGoogle(); await syncGoogleCalendar(session); await loadOpportunities('opportunity-list'); status('workspace-status', 'Calendario aggiornato.', 'success'); }
async function scheduleCalendarEvent(event) { event.preventDefault(); const form = event.currentTarget; const days = Array.from(form.querySelectorAll('input[name="schedule-day"]:checked')).map(input => input.value); const start = $('schedule-start').value; const end = $('schedule-end').value; if (!days.length) return status('calendar-schedule-status', 'Seleziona almeno un giorno.', 'error'); if (!start || !end || new Date(end) <= new Date(start)) return status('calendar-schedule-status', 'Controlla data e orario di inizio e fine.', 'error'); const button = form.querySelector('button[type="submit"]'); button.disabled = true; status('calendar-schedule-status', 'Creo la serie di eventi su Google Calendar...', 'loading'); try { const response = await fetch('/api/calendar/events/recurring', { method: 'POST', headers: { ...(await authHeaders()), 'Content-Type': 'application/json' }, body: JSON.stringify({ title: $('schedule-title').value.trim(), description: $('schedule-description').value.trim(), start: `${start}:00`, end: `${end}:00`, frequency: form.querySelector('input[name="schedule-frequency"]:checked').value, days, weeks: Number($('schedule-weeks').value) }) }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Pianificazione non riuscita.'); status('calendar-schedule-status', 'Serie di eventi pianificata su Google Calendar.', 'success'); await loadOpportunities('opportunity-list'); } catch (error) { status('calendar-schedule-status', error.message, 'error'); } finally { button.disabled = false; } }
function setupCalendarShell() { const planner = $('calendar-planner'); if (!planner || document.querySelector('.calendar-shell')) return; const shell = document.createElement('div'); shell.className = 'calendar-shell panel'; shell.innerHTML = '<div class="calendar-toolbar"><button class="icon-button" id="calendar-previous" type="button" aria-label="Settimana precedente">&lt;</button><h2 id="calendar-month-label"></h2><button class="icon-button" id="calendar-next" type="button" aria-label="Settimana successiva">&gt;</button><button class="btn btn-secondary calendar-today" id="calendar-today" type="button">Oggi</button><button class="btn btn-danger" id="calendar-delete-selected" type="button" disabled>Elimina selezionati</button></div><div id="calendar-grid" class="calendar-week-grid"></div>'; planner.parentNode.insertBefore(shell, planner); planner.classList.add('hidden'); $('calendar-previous').addEventListener('click', () => { calendarMonth.setDate(calendarMonth.getDate() - 7); loadCalendarMonth(); }); $('calendar-next').addEventListener('click', () => { calendarMonth.setDate(calendarMonth.getDate() + 7); loadCalendarMonth(); }); $('calendar-today').addEventListener('click', () => { calendarMonth = new Date(); loadCalendarMonth(); }); $('calendar-delete-selected').addEventListener('click', deleteSelectedCalendarEvents); }
async function connectMeta() { if (!supabaseClient) return status('workspace-status', 'Accedi prima con Google.', 'error'); const { data: { session } } = await supabaseClient.auth.getSession(); if (!session) return status('workspace-status', 'Accedi prima con Google.', 'error'); const response = await fetch('/api/auth/meta/start', { headers: { Authorization: `Bearer ${session.access_token}` } }); const data = await response.json(); if (!response.ok) return status('workspace-status', data.detail || 'Meta non configurato.', 'error'); window.location.href = data.url; }
async function authHeaders() {
  if (!supabaseClient) return {};
  const { data: { session } } = await supabaseClient.auth.getSession();
  if (!session) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}
async function loadAnalytics() { try { const response = await fetch('/api/analytics', { headers: await authHeaders() }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Analytics non disponibili.'); $('today-reach').textContent = Number(data.total_reach).toLocaleString(); $('kpi-top-er').textContent = `${data.posts[0]?.engagement_rate || 0}%`; $('kpi-avg-er').textContent = `${data.avg_engagement_rate}%`; $('kpi-avg-save').textContent = `${data.avg_save_rate}%`; $('kpi-reach').textContent = Number(data.total_reach).toLocaleString(); renderCharts(data.posts); renderPosts(data.posts); } catch (error) { $('connection-status').textContent = error.message; } }
function renderCharts(posts) { const labels = posts.map(post => `#${post.id}`); if (erChartInstance) erChartInstance.destroy(); erChartInstance = new Chart($('chart-er').getContext('2d'), { type: 'bar', data: { labels, datasets: [{ data: posts.map(post => post.engagement_rate), backgroundColor: '#d66b4d', borderRadius: 3 }] }, options: { responsive: true, plugins: { legend: { display: false } } } }); if (matrixChartInstance) matrixChartInstance.destroy(); matrixChartInstance = new Chart($('chart-matrix').getContext('2d'), { type: 'scatter', data: { datasets: [{ data: posts.map(post => ({ x: post.save_rate, y: post.conversation_rate })), backgroundColor: '#386f68', pointRadius: 7 }] }, options: { responsive: true, plugins: { legend: { display: false } } } }); }
function renderPosts(posts) { const container = $('posts-container'); container.replaceChildren(); posts.forEach(post => { const item = document.createElement('article'); item.className = 'post-item'; const image = document.createElement('img'); image.className = 'post-img'; image.src = post.local_files?.[0] || post.media_url; image.alt = 'Contenuto pubblicato'; const details = document.createElement('div'); details.className = 'post-details'; const title = document.createElement('h3'); title.textContent = `${post.media_type} - ${post.date}`; const caption = document.createElement('p'); caption.className = 'post-caption'; caption.textContent = post.caption; details.append(title, caption); item.append(image, details); container.appendChild(item); }); }
async function loadHome() { await Promise.all([loadOpportunities(), loadContent(), loadAnalytics()]); }
document.querySelectorAll('.nav-btn, [data-view-target]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view || button.dataset.viewTarget)));
$('photo-input').addEventListener('change', event => { selectedFiles = Array.from(event.target.files); renderPreview(); });
$('copy-form').addEventListener('submit', generateCopy);
$('google-connect').addEventListener('click', connectGoogle);
$('calendar-schedule-form').addEventListener('submit', scheduleCalendarEvent);
$('calendar-previous')?.addEventListener('click', () => { calendarMonth.setMonth(calendarMonth.getMonth() - 1); loadCalendarMonth(); });
$('calendar-next')?.addEventListener('click', () => { calendarMonth.setMonth(calendarMonth.getMonth() + 1); loadCalendarMonth(); });
$('calendar-today')?.addEventListener('click', () => { calendarMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1); loadCalendarMonth(); });
$('calendar-delete-selected')?.addEventListener('click', deleteSelectedCalendarEvents);
$('close-calendar-planner')?.addEventListener('click', () => $('calendar-planner').classList.add('hidden'));
$('refresh-content').addEventListener('click', loadContent);
$('refresh-analytics').addEventListener('click', loadAnalytics);
$('login-google').addEventListener('click', loginWithGoogle);
$('meta-connect').addEventListener('click', connectMeta);
$('logout').addEventListener('click', logout);
window.addEventListener('DOMContentLoaded', async () => { setupCalendarShell(); await initializeAuth(); const opportunities = $('view-opportunities'); if (opportunities?.classList.contains('active')) loadCalendarMonth(); loadHome(); });

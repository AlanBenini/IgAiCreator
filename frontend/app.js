let erChartInstance = null;
let matrixChartInstance = null;
let selectedFiles = [];
let supabaseClient = null;
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
  supabaseClient.auth.onAuthStateChange((_event, nextSession) => updateAuthUI(nextSession));
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
  await supabaseClient.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin } });
}
async function logout() { if (supabaseClient) await supabaseClient.auth.signOut(); }

function switchView(view) {
  document.querySelectorAll('.view').forEach(element => element.classList.toggle('active', element.id === `view-${view}`));
  document.querySelectorAll('.nav-btn').forEach(element => element.classList.toggle('active', element.dataset.view === view));
  if (view === 'today') loadHome();
  if (view === 'opportunities') loadOpportunities('opportunity-list');
  if (view === 'content') loadContent();
  if (view === 'performance') loadAnalytics();
}
function status(id, message, type = '') { const element = $(id); element.textContent = message; element.className = `status-msg ${type}`; }
function priorityClass(priority) { return priority === 'ALTA' ? 'priority-high' : priority === 'MEDIA' ? 'priority-medium' : 'priority-low'; }
function renderOpportunity(item) { const element = document.createElement('article'); element.className = 'opportunity-card'; element.innerHTML = `<div class="opportunity-top"><span class="priority ${priorityClass(item.priority)}">${item.priority}</span><span class="opportunity-date">${item.start.slice(0, 16).replace('T', ' ')}</span></div><h3></h3><p></p><div class="brief-list"></div>`; element.querySelector('h3').textContent = item.title; element.querySelector('p').textContent = item.reason; item.capture_brief.forEach(line => { const brief = document.createElement('span'); brief.textContent = line; element.querySelector('.brief-list').appendChild(brief); }); return element; }
async function readResponse(response) { const raw = await response.text(); try { return raw ? JSON.parse(raw) : {}; } catch { return { detail: raw || `Errore HTTP ${response.status}` }; } }
async function loadOpportunities(targetId = 'today-opportunities') { const container = $(targetId); container.replaceChildren(); try { const response = await fetch('/api/opportunities', { headers: await authHeaders() }); const data = await readResponse(response); if (!response.ok) throw new Error(data.detail || 'Calendario non disponibile.'); if (!data.length) { container.innerHTML = '<p class="empty-state">Collega Google Calendar per attivare il Content Radar.</p>'; return; } data.forEach(item => container.appendChild(renderOpportunity(item))); } catch (error) { container.innerHTML = `<p class="empty-state">${error.message}</p>`; } }
function renderPreview() { const container = $('photo-preview'); container.replaceChildren(); selectedFiles.forEach((file, index) => { const item = document.createElement('div'); item.className = 'preview-item'; const image = document.createElement('img'); image.src = URL.createObjectURL(file); image.alt = file.name; const label = document.createElement('span'); label.textContent = `${String(index + 1).padStart(2, '0')} - ${file.name}`; item.append(image, label); container.appendChild(item); }); }
async function generateCopy(event) { event.preventDefault(); if (!selectedFiles.length) return status('workspace-status', 'Scegli almeno una foto.', 'error'); const button = $('generate-button'); button.disabled = true; status('workspace-status', "L'AI sta analizzando il materiale e preparando la proposta...", 'loading'); const form = new FormData(); selectedFiles.forEach(file => form.append('files', file)); form.append('theme', $('theme').value.trim()); form.append('content_format', $('content-format').value); form.append('title', $('content-title').value.trim() || 'Nuovo momento aziendale'); try { const response = await fetch('/api/generate-copy', { method: 'POST', body: form }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Generazione non riuscita.'); renderCopies(data.copies, data.assets, data.draft); $('copy-results').classList.remove('hidden'); loadContent(); status('workspace-status', 'Bozza creata e pronta per approvazione.', 'success'); } catch (error) { status('workspace-status', error.message, 'error'); } finally { button.disabled = false; } }
function renderCopies(copies, assets, draft) { const container = $('copy-container'); container.replaceChildren(); copies.forEach((copy, index) => { const card = document.createElement('article'); card.className = 'copy-card'; const image = document.createElement('img'); image.src = assets[index].url; image.alt = assets[index].filename; const heading = document.createElement('div'); heading.className = 'copy-heading'; const title = document.createElement('h3'); title.textContent = `Output ${String(index + 1).padStart(2, '0')}`; const badge = document.createElement('span'); badge.textContent = draft.status; heading.append(title, badge); const hook = document.createElement('p'); hook.className = 'copy-hook'; hook.textContent = copy.hook; const caption = document.createElement('p'); caption.className = 'copy-caption'; caption.textContent = copy.caption; const tags = document.createElement('p'); tags.className = 'copy-tags'; tags.textContent = copy.hashtags.join(' '); card.append(image, heading, hook, caption, tags); container.appendChild(card); }); }
function renderContentItem(item) { const element = document.createElement('article'); element.className = 'content-row'; element.innerHTML = `<div><span class="content-format"></span><h3></h3><p></p></div><div class="content-actions"><span class="status-pill"></span><button class="btn btn-secondary approve-button">Approva</button><button class="btn btn-primary publish-button">Pubblica</button></div>`; element.querySelector('.content-format').textContent = item.content_format.replaceAll('_', ' '); element.querySelector('h3').textContent = item.title; element.querySelector('p').textContent = new Date(item.created_at).toLocaleString('it-IT'); element.querySelector('.status-pill').textContent = item.status; const approve = element.querySelector('.approve-button'); const publish = element.querySelector('.publish-button'); approve.disabled = item.status !== 'DRAFT'; publish.disabled = item.status !== 'APPROVED'; approve.addEventListener('click', () => updateContent(item.id, 'APPROVED')); publish.addEventListener('click', () => publishContent(item.id)); return element; }
async function loadContent() { const container = $('content-library'); if (!container) return; container.replaceChildren(); try { const response = await fetch('/api/content'); const data = await response.json(); $('draft-count').textContent = data.filter(item => item.status === 'DRAFT').length; if (!data.length) { container.innerHTML = '<p class="empty-state">Nessuna bozza. Carica un momento dal Content Studio.</p>'; return; } data.forEach(item => container.appendChild(renderContentItem(item))); renderTodayContent(data); } catch (error) { container.innerHTML = `<p class="empty-state">${error.message}</p>`; } }
function renderTodayContent(items) { const container = $('today-content'); if (!container) return; container.replaceChildren(); items.slice(0, 3).forEach(item => container.appendChild(renderContentItem(item))); if (!items.length) container.innerHTML = '<p class="empty-state">Ancora nessun contenuto creato.</p>'; }
async function updateContent(id, nextStatus) { const response = await fetch(`/api/content/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: nextStatus }) }); if (!response.ok) { const data = await response.json(); return status('workspace-status', data.detail || 'Aggiornamento non riuscito.', 'error'); } loadContent(); }
async function publishContent(id) { const response = await fetch(`/api/content/${id}/publish`, { method: 'POST', headers: await authHeaders() }); const data = await response.json(); if (!response.ok) return status('workspace-status', data.detail || 'Pubblicazione non riuscita.', 'error'); loadContent(); status('workspace-status', 'Contenuto pubblicato.', 'success'); }
async function connectGoogle() { try { const response = await fetch('/api/calendar/auth', { headers: await authHeaders() }); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Google non configurato.'); window.location.href = data.url; } catch (error) { alert(error.message); } }
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
$('refresh-content').addEventListener('click', loadContent);
$('refresh-analytics').addEventListener('click', loadAnalytics);
$('login-google').addEventListener('click', loginWithGoogle);
$('meta-connect').addEventListener('click', connectMeta);
$('logout').addEventListener('click', logout);
window.addEventListener('DOMContentLoaded', async () => { await initializeAuth(); loadHome(); });

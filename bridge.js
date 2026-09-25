/* Backend adapter. Keeps the original UI and import wizard. */
let serverRevision = 0, savedBase = null, saving = null, persistenceReady = false;
let saveError = '', aiAvailable = false;

function buildEmptyState() {
  return {
    masterData: {locations: [], legalEntities: [], sources: [], params: []},
    metrics: METRICS.map(m => ({...m})), qualitativeMetrics: QUALITATIVE_METRICS.map(m => ({...m})),
    measures: [], actuals: [], targets: [], qualitative: [], imports: [], auditLog: [],
    customDimensions: [], dimensions: STD_DIMENSIONS.map(d => ({...d})),
    integrations: buildIntegrations().map(i => ({...i, records: 0, lastSync: '—', status: i.id === 'INT-XLS' ? 'Active' : 'Not configured'})),
    frameworks: FRAMEWORKS.map(f => ({...f, status: 'Demo', coverage: 0, dps: 0})), report: null
  };
}

async function api(path, method = 'GET', body) {
  const response = await fetch('/api/' + path, {
    method, credentials: 'same-origin', cache: 'no-store',
    headers: {'Content-Type': 'application/json', 'X-IVZ-Request': '1'},
    body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(typeof result.detail === 'string' ? result.detail : 'No se pudo completar la solicitud (' + response.status + ')'), {status: response.status});
  return result;
}

function saveStatus(text, failed = false) {
  const target = document.getElementById('server-status');
  if (target) { target.textContent = text; target.style.color = failed ? '#b42318' : '#356447'; }
}

/* The server keeps measures and actuals as rows: the screen loads them in pages and saves only
   what changed, so no request carries the whole dataset (Vercel caps requests at 4.5 MB). */
const ROW_KINDS = ['measures', 'actuals'], ROW_PAGE = 2000, PART_BYTES = 2500000;
const EMPTY_BASE = {rest: '', rows: {measures: new Map(), actuals: new Map()}, ids: {measures: [], actuals: []}};

// Changes since the last save, and the baseline to keep once they are saved. `initial` only builds the baseline.
function stateDiff(st = appState, initial = false) {
  const {measures, actuals, ...rest} = st;
  const next = {rest: JSON.stringify(rest), rows: {}, ids: {}}, out = {changes: {}, order: {}};
  let any = false;
  if (!initial && next.rest !== savedBase.rest) { out.catalogue = next.rest; any = true; }
  for (const kind of ROW_KINDS) {
    const list = st[kind] || [], rows = new Map(), upsert = [];
    for (const row of list) {
      // Rows are saved one by one, so each needs its own id.
      if (typeof row.id !== 'string' || !row.id || rows.has(row.id)) row.id = (kind === 'measures' ? 'MSR-' : 'ACT-') + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      const json = JSON.stringify(row);
      rows.set(row.id, json);
      if (!initial && savedBase.rows[kind].get(row.id) !== json) upsert.push(json);
    }
    const ids = list.map(row => row.id);
    next.rows[kind] = rows; next.ids[kind] = ids;
    if (initial) continue;
    const removed = [...savedBase.rows[kind].keys()].filter(id => !rows.has(id));
    if (upsert.length || removed.length) { out.changes[kind] = {upsert, delete: removed}; any = true; }
    // New rows go to the end on the server; send the full order only when they were placed elsewhere or rows moved.
    const expected = savedBase.ids[kind].filter(id => rows.has(id)).concat(ids.filter(id => !savedBase.rows[kind].has(id)));
    if (ids.some((id, i) => id !== expected[i])) { out.order[kind] = ids; any = true; }
  }
  return {any, out, next};
}

const rowsJson = changes => '{' + Object.entries(changes).map(([kind, c]) => JSON.stringify(kind) + ':{"upsert":[' + c.upsert.join(',') + ']' +
  (c.delete ? ',"delete":' + JSON.stringify(c.delete) : '') + '}').join(',') + '}';

async function sendChanges(out) {
  // A large change set travels in parts; the final request names the batch and the server applies it all at once.
  let batch = null, parts = 0, chunk = {}, chunkBytes = 0;
  const total = Object.values(out.changes).reduce((n, c) => n + c.upsert.reduce((m, json) => m + json.length, 0), 0);
  if (total > PART_BYTES) {
    batch = 'b' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    const flush = async () => {
      if (!chunkBytes) return;
      await api('state/upload', 'POST', '{"batch":"' + batch + '","part":' + parts + ',"changes":' + rowsJson(chunk) + '}');
      parts++; chunk = {}; chunkBytes = 0;
    };
    for (const [kind, c] of Object.entries(out.changes)) {
      for (const json of c.upsert) {
        if (chunkBytes + json.length > PART_BYTES) await flush();
        (chunk[kind] ??= {upsert: []}).upsert.push(json); chunkBytes += json.length;
      }
      c.upsert = [];
    }
    await flush();
  }
  let body = '{"revision":' + serverRevision + ',"changes":' + rowsJson(out.changes) + ',"order":' + JSON.stringify(out.order);
  if (out.catalogue) body += ',"catalogue":' + out.catalogue;
  if (batch) body += ',"batch":"' + batch + '","parts":' + parts;
  return api('state/changes', 'POST', body + '}');
}

async function loadState() {
  for (let attempt = 0; attempt < 3; attempt++) {
    const data = await api('state/catalogue');
    if (!data.state) return data;
    const state = {...data.state};
    let consistent = true;
    for (const kind of ROW_KINDS) {
      state[kind] = [];
      for (let offset = 0; consistent && offset < data.counts[kind]; offset += ROW_PAGE) {
        const page = await api('state/rows?kind=' + kind + '&offset=' + offset + '&limit=' + ROW_PAGE);
        if (page.revision !== data.revision) consistent = false; else for (const row of page.items) state[kind].push(row);
      }
    }
    if (consistent) return {...data, state};
  }
  throw new Error('Los datos cambiaron mientras se cargaban. Recargá la página.');
}

/* Closed years: their measures and actuals cannot be edited until an administrator reopens them.
   The server enforces it; the screen undoes such an edit right away instead of failing to save. */
let closures = [], closedYears = new Set(), impersonating = false, access = 'admin';
const readOnly = () => access === 'viewer';
async function loadClosures() { closures = await api('closures'); closedYears = new Set(closures.map(c => c.year)); }

function enforceClosed() {
  if (!closedYears.size || !savedBase) return;
  let reverted = 0;
  for (const kind of ROW_KINDS) {
    const list = appState[kind] || [], seen = new Set();
    for (let i = list.length - 1; i >= 0; i--) {
      const row = list[i], json = savedBase.rows[kind].get(row.id), old = json && JSON.parse(json);
      seen.add(row.id);
      if (!closedYears.has(row.y) && !(old && closedYears.has(old.y))) continue;
      if (json === JSON.stringify(row)) continue;
      if (old) list[i] = old; else list.splice(i, 1);
      reverted++;
    }
    for (const [id, json] of savedBase.rows[kind]) if (!seen.has(id)) { const old = JSON.parse(json); if (closedYears.has(old.y)) { list.push(old); reverted++; } }
  }
  if (reverted) { destroyCharts(); render(); toast('Ese año está cerrado: el cambio no se guardó.', 'warn'); }
}

function renderYears(w) {
  const box = w.querySelector('#account-years');
  if (!box) return;
  const years = [...new Set(ROW_KINDS.flatMap(k => (appState[k] || []).map(r => r.y)).filter(Number.isInteger))].sort();
  const byYear = Object.fromEntries(closures.map(c => [c.year, c]));
  box.innerHTML = '<h4 style="margin:18px 0 4px">Cierre de años</h4><p class="muted" style="margin:0 0 6px;font-size:12.5px">Un año cerrado no se puede editar. Solo un administrador de Invenzis puede reabrirlo.</p>' +
    years.map(y => {
      const c = byYear[y];
      return '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--line)"><span><b>' + y + '</b> · ' +
        (c ? 'Cerrado el ' + esc(new Date(c.closedAt).toLocaleDateString('es')) + ' por ' + esc(c.closedBy) : 'Abierto') + '</span>' +
        (readOnly() ? '' : c ? (impersonating ? '<button class="btn" data-reopen="' + y + '">Reabrir</button>' : '') : '<button class="btn" data-close="' + y + '">Cerrar año</button>') + '</div>';
    }).join('') + '<p id="years-error" role="alert" style="color:var(--bad);min-height:18px;margin:6px 0 0"></p>';
  box.querySelectorAll('[data-close]').forEach(b => b.onclick = () => closeYear(+b.dataset.close, b, w));
  box.querySelectorAll('[data-reopen]').forEach(b => b.onclick = () => reopenYear(+b.dataset.reopen, b, w));
}

async function closeYear(year, button, w) {
  const rows = ROW_KINDS.reduce((n, k) => n + (appState[k] || []).filter(r => r.y === year).length, 0);
  if (!confirm('¿Cerrar ' + year + '? ' + fmt(rows) + ' registros.\n\nDespués no se van a poder agregar, editar ni borrar mediciones ni valores reales de ese año.')) return;
  button.disabled = true;
  try { await persist(); await api('closures', 'POST', {year}); await loadClosures(); renderYears(w); toast(year + ' cerrado.'); }
  catch (e) { button.disabled = false; w.querySelector('#years-error').textContent = e.message; }
}

async function reopenYear(year, button, w) {
  const reason = prompt('Motivo para reabrir ' + year + ' (queda registrado):');
  if (!reason || !reason.trim()) return;
  button.disabled = true;
  try { await api('closures/' + year + '/reopen', 'POST', {reason: reason.trim()}); await loadClosures(); renderYears(w); toast(year + ' reabierto.'); }
  catch (e) { button.disabled = false; w.querySelector('#years-error').textContent = e.message; }
}

// What the server has, rebuilt from the baseline of the last load or save.
function savedState() {
  const st = JSON.parse(savedBase.rest);
  for (const kind of ROW_KINDS) st[kind] = savedBase.ids[kind].map(id => JSON.parse(savedBase.rows[kind].get(id)));
  return st;
}

// A viewer can look but not change: an edit on screen is undone instead of being sent (the server refuses it anyway).
function undoForViewer() {
  if (!stateDiff().any) return;
  const saved = savedState();
  for (const key of Object.keys(appState)) delete appState[key];
  Object.assign(appState, saved);
  destroyCharts(); render();
  toast('Tu usuario es de solo lectura: los cambios no se guardan.', 'warn');
}

async function persist() {
  if (!persistenceReady) return;
  if (readOnly()) return undoForViewer();
  if (saving) { await saving; return persist(); }
  enforceClosed();
  const {any, out, next} = stateDiff();
  if (!any) return;
  saveStatus('Guardando…');
  saving = sendChanges(out).then(result => {
    serverRevision = result.revision; savedBase = next; saveError = '';
    saveStatus('Guardado · revisión ' + serverRevision);
  }).catch(error => {
    if (error.status === 423) return loadClosures().then(enforceClosed);  // closed from another tab
    saveError = error.message; saveStatus('Sin guardar: ' + error.message, true); throw error;
  }).finally(() => { saving = null; });
  await saving;
}

function showLogin(message = '') {
  document.getElementById('boot-gate')?.remove();
  const screen = document.createElement('div');
  screen.id = 'login-screen';
  screen.style.cssText = 'position:fixed;inset:0;background:var(--bg);color:var(--ink);font:16px system-ui;z-index:99999;display:grid;place-items:center';
  screen.innerHTML = '<main style="background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:44px;width:min(440px,94vw);box-shadow:var(--sh-lg)">' +
    '<h1 style="margin:0 0 8px;font-size:26px">IVZ Sustainability Hub</h1><p style="color:var(--ink-2);line-height:1.5;margin:0">Sistema Tenant Invenzis</p>' +
    '<form><label style="display:block;margin-top:20px">Usuario<input name="username" autocomplete="username" required style="font:inherit;width:100%;padding:12px;border-radius:8px;border:1px solid var(--line-2);margin-top:8px"></label>' +
    '<label style="display:block;margin-top:20px">Contraseña<input type="password" name="password" autocomplete="current-password" required style="font:inherit;width:100%;padding:12px;border-radius:8px;border:1px solid var(--line-2);margin-top:8px"></label>' +
    '<p id="login-error" role="alert" style="color:var(--bad);min-height:24px;margin:8px 0 0"></p>' +
    '<button type="submit" style="font:inherit;width:100%;padding:12px;border-radius:8px;border:0;background:var(--accent);color:white;cursor:pointer;margin-top:8px">Ingresar</button></form></main>';
  document.body.appendChild(screen);
  screen.querySelector('#login-error').textContent = message;
  screen.querySelector('form').onsubmit = async event => {
    event.preventDefault(); const form = event.target; const button = form.querySelector('button'); button.disabled = true;
    try {
      await api('login', 'POST', {username: form.username.value, password: form.password.value});
      await boot(); screen.remove();
    } catch (error) { screen.querySelector('#login-error').textContent = error.message; }
    finally { button.disabled = false; }
  };
}

// Corrige catálogos de métricas persistidos antes del cambio de unidad de energía (GWh -> kWh).
function migrateEnergyUnit(state) {
  const m = state && (state.metrics || []).find(x => x.id === 'ENV-ENERGY');
  if (m && m.unit === 'GWh') {
    m.unit = 'kWh'; delete m.scale; m.formula = 'SUM(Energy consumption)';
    return true;
  }
  return false;
}

function showImpersonationBar() {
  const bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:100000;background:#8a5a1a;color:#fff;font:13px system-ui;display:flex;align-items:center;justify-content:center;gap:14px;padding:8px 16px';
  bar.innerHTML = '<span>Estás viendo esta cuenta como administrador.</span>';
  const back = document.createElement('button');
  back.textContent = 'Volver a administración';
  back.style.cssText = 'font:inherit;cursor:pointer;border-radius:6px;border:1px solid #fff6;background:transparent;color:#fff;padding:4px 10px';
  back.onclick = async () => {
    back.disabled = true;
    try { await api('admin/return', 'POST'); location.replace('/admin'); }
    catch (e) { back.disabled = false; }
  };
  bar.append(back); document.body.prepend(bar);
  document.body.style.paddingTop = '36px';
}

async function boot() {
  const data = await loadState();
  impersonating = !!data.impersonating;
  access = data.access || 'viewer';
  if (data.state) await loadClosures();
  if (data.role === 'admin' && !data.impersonating) { location.replace('/admin'); return; }
  aiAvailable = data.ai;
  if (data.features) FEATURES = data.features;  // secciones e integraciones habilitadas por el administrador
  CONFIG.USER.name = data.username;
  CONFIG.USER.role = data.company;
  filters.loc = 'ALL';
  const base = data.state ? stateDiff(data.state, true).next : EMPTY_BASE;
  const state = data.state || buildEmptyState();
  const energyMigrated = migrateEnergyUnit(state);
  init(state);
  document.getElementById('profile-name').textContent = data.username;
  document.getElementById('profile-company').textContent = data.company;
  document.getElementById('profile-avatar').textContent = data.username.slice(0, 2).toUpperCase();
  if (data.impersonating) showImpersonationBar();
  // One general template in this demo. Other frameworks are future work.
  appState.integrations.forEach(i => { if (i.id !== 'INT-XLS') { i.status = 'Not configured'; i.records = 0; i.lastSync = '—'; } });
  serverRevision = data.revision;
  // A viewer's baseline is what it sees after loading, so nothing of its own counts as a change.
  savedBase = readOnly() ? stateDiff(appState, true).next : base;
  persistenceReady = !(readOnly() && !data.state);
  if (readOnly()) document.getElementById('profile-company').textContent = data.company + ' · Solo lectura';
  destroyCharts(); render();
  document.getElementById('boot-gate')?.remove();
  if (energyMigrated && !readOnly()) persist().catch(e => toast(e.message, 'bad'));
  await persist();
}

const originalReportEditor = renderReportEditor;
const originalMasterView = viewMaestros;
viewMaestros = function(el) {
  originalMasterView(el);
  if (ui.tab.maestros === 'ent') {
    const button = document.createElement('button');
    button.className = 'btn pri'; button.textContent = 'Nueva entidad legal';
    document.getElementById('md-body').before(button);
    button.onclick = () => modal({title: 'Nueva entidad legal', body:
      '<div class="field"><label>ID<input class="inp" id="entity-id"></label></div>' +
      '<div class="field"><label>Nombre<input class="inp" id="entity-name"></label></div>' +
      '<div class="field"><label>País<input class="inp" id="entity-country"></label></div>' +
      '<div class="field"><label>Identificación fiscal<input class="inp" id="entity-tax"></label></div>',
      footer: '<button class="btn" data-close>Cancelar</button><button class="btn pri" id="entity-save">Crear entidad</button>',
      onMount: w => w.querySelector('#entity-save').onclick = async () => {
        const get = id => w.querySelector(id).value.trim();
        const id = get('#entity-id').toUpperCase(), name = get('#entity-name');
        if (!id || !name || appState.masterData.legalEntities.some(e => e.id === id)) {toast('Ingresá ID único y nombre.', 'warn'); return;}
        appState.masterData.legalEntities.push({id, name, country: '', countryName: get('#entity-country'), taxId: get('#entity-tax'), role: ''});
        closeModal(); render();
        try {await persist();} catch(e) {toast(e.message, 'bad');}
      }
    });
  }
  if (!appState.masterData.locations.length && (ui.tab.maestros || 'org') === 'org') {
    document.getElementById('md-tree').textContent = 'Sin ubicaciones. Creá tu primer nodo con el botón Nodo.';
    const canvas = document.getElementById('ch-org');
    if (canvas) canvas.parentElement.innerHTML = '<p class="muted">Sin ubicaciones configuradas.</p>';
  }
};
// Edits to a previously approved document return it to draft until approved again.
document.addEventListener('input', e => {
  if (appState && appState.report && e.target.closest('#rep-doc')) appState.report.meta.status = 'Draft';
});
openIntegration = () => toast('Integración prevista para una etapa posterior. Usá las importaciones de archivos.', 'warn');

filterActuals = function (st, metricId, opts) {
  const o = opts || {}, locs = o.locs || scopeLocs(st, o.loc || 'GRP'), months = periodMonths(o.period);
  let rows = st.actuals.filter(r => r.metricId === metricId && locs.includes(r.loc) &&
    (!o.year || r.y === o.year) && (!o.years || o.years.includes(r.y)) &&
    (!o.entity || o.entity === 'ALL' || r.entity === o.entity));
  const monthly = new Set(rows.filter(r => r.periodType === 'M').map(r => r.loc + ':' + r.y));
  return rows.filter(r => months ? r.periodType === 'M' && months.includes(r.m) :
    r.periodType === (monthly.has(r.loc + ':' + r.y) ? 'M' : 'Y'));
};
openUserMenu = () => modal({
  title: 'Configuración de la cuenta', icon: 'settings',
  body: '<dl class="kv"><dt>Usuario</dt><dd>' + esc(CONFIG.USER.name) + '</dd><dt>Organización</dt><dd>' + esc(CONFIG.USER.role) + '</dd>' +
    '<dt>Rol</dt><dd>' + esc(IVZUsers.ROLES[access] || access) + '</dd></dl><div id="account-years"></div>' +
    (access === 'admin' ? '<h4 style="margin:18px 0 4px">Usuarios de la empresa</h4><div id="account-users"></div>' : ''),
  footer: '<button class="btn" data-close>Volver</button><button class="btn" id="account-logout"><i data-lucide="log-out"></i>Cerrar sesión</button>',
  onMount: w => {
    renderYears(w);
    const people = w.querySelector('#account-users');
    if (people) IVZUsers.mount(people, {base: '/api/users', headers: {'X-IVZ-Request': '1'}, me: CONFIG.USER.name});
    w.querySelector('#account-logout').onclick = async event => {
      const button = event.currentTarget; button.disabled = true;
      try { await persist(); await api('logout', 'POST'); persistenceReady = false; location.reload(); }
      catch (e) { button.disabled = false; toast(e.message, 'bad'); }
    };
  }
});

window.addEventListener('beforeunload', event => {
  if (persistenceReady && stateDiff().any) {event.preventDefault(); event.returnValue = '';}
});
setInterval(() => { if (persistenceReady && !saveError) persist().catch(() => {}); }, 2500);
document.addEventListener('DOMContentLoaded', () => boot().catch(() => showLogin()));

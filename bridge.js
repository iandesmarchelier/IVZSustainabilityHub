/* Backend adapter. Keeps the original UI and import wizard. */
let serverRevision = 0, savedState = '', saving = null, persistenceReady = false;
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
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'No se pudo completar la solicitud (' + response.status + ')');
  return result;
}

function saveStatus(text, failed = false) {
  const target = document.getElementById('server-status');
  if (target) { target.textContent = text; target.style.color = failed ? '#b42318' : '#356447'; }
}

async function persist() {
  if (!persistenceReady) return;
  if (saving) { await saving; return persist(); }
  const body = JSON.stringify(appState);
  if (body === savedState) return;
  saveStatus('Guardando…');
  saving = api('state', 'PUT', {revision: serverRevision, state: appState}).then(result => {
    serverRevision = result.revision; savedState = body; saveError = '';
    saveStatus('Guardado · revisión ' + serverRevision);
  }).catch(error => {
    saveError = error.message; saveStatus('Sin guardar: ' + error.message, true); throw error;
  }).finally(() => { saving = null; });
  await saving;
}

function showLogin(message = '') {
  const screen = document.createElement('div');
  screen.id = 'login-screen';
  screen.style.cssText = 'position:fixed;inset:0;background:#f2f5f1;z-index:99999;display:grid;place-items:center';
  screen.innerHTML = '<form style="width:360px;max-width:90vw;padding:32px;background:white;border-radius:16px;box-shadow:0 8px 40px #0001">' +
    '<h2>IVZ Sustainability Hub</h2><p>Sistema Tenant Invenzis</p><label>Usuario de empresa<input name="username" autocomplete="username" required class="inp" style="width:100%;margin:8px 0 16px"></label>' +
    '<label>Contraseña<input type="password" name="password" autocomplete="current-password" required class="inp" style="width:100%;margin:8px 0 16px"></label>' +
    '<p id="login-error" style="color:#b42318"></p><button class="btn pri" type="submit">Ingresar</button></form>';
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

async function boot() {
  const data = await api('state');
  aiAvailable = data.ai;
  CONFIG.USER.name = data.username;
  CONFIG.USER.role = data.company;
  filters.loc = 'ALL';
  const savedJson = data.state ? JSON.stringify(data.state) : '';
  const state = data.state || buildEmptyState();
  const energyMigrated = migrateEnergyUnit(state);
  init(state);
  document.querySelector('#btn-user').textContent = data.username + ' · ' + data.company;
  // One general template in this demo. Other frameworks are future work.
  appState.integrations.forEach(i => { if (i.id !== 'INT-XLS') { i.status = 'Not configured'; i.records = 0; i.lastSync = '—'; } });
  serverRevision = data.revision;
  savedState = savedJson;
  persistenceReady = true;
  destroyCharts(); render();
  if (energyMigrated) persist().catch(e => toast(e.message, 'bad'));
  if (!document.getElementById('backend-tools')) {
    const bar = document.createElement('div'); bar.id = 'backend-tools';
    bar.className = 'no-print';
    bar.style.cssText = 'position:fixed;bottom:12px;right:16px;z-index:150;background:white;padding:10px 14px;border:1px solid #ccc;border-radius:10px;display:flex;gap:12px;align-items:center;max-width:90vw';
    bar.innerHTML = '<small id="server-status">Guardado</small><button class="btn" id="server-save">Guardar</button><button class="btn" id="server-history">Reportes guardados</button><button class="btn" id="server-logout">Salir</button>';
    document.body.appendChild(bar);
    document.getElementById('server-save').onclick = () => persist().catch(e => toast(e.message, 'bad'));
    document.getElementById('server-logout').onclick = async () => {
      try { await persist(); await api('logout', 'POST'); location.reload(); }
      catch (e) { toast(e.message, 'bad'); }
    };
    document.getElementById('server-history').onclick = async () => {
      try {
        const reports = await api('reports');
        modal({title: 'Reportes guardados', body: reports.length ? reports.map((r, i) =>
          '<p><button class="btn" data-history="' + i + '">' + esc(r.meta.title) + ' · ' + esc(r.meta.status) + '</button></p>').join('') : '<p>No hay reportes.</p>',
          footer: '<button class="btn" data-close>Cerrar</button>', onMount: w => {
            w.querySelectorAll('[data-history]').forEach(b => b.onclick = () => {appState.report = reports[+b.dataset.history]; closeModal(); ui.tab.reportes = 'editor'; go('reportes');});
          }});
      } catch (e) { toast(e.message, 'bad'); }
    };
  }
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
openUserMenu = () => modal({title: 'Cuenta de empresa', body: '<p>' + esc(CONFIG.USER.name) + '</p><p>' + esc(CONFIG.USER.role) + '</p><p>Datos guardados en el servidor. Sesión de hasta 8 horas.</p>', footer: '<button class="btn" data-close>Cerrar</button>'});

window.addEventListener('beforeunload', event => {
  if (persistenceReady && JSON.stringify(appState) !== savedState) {event.preventDefault(); event.returnValue = '';}
});
setInterval(() => { if (persistenceReady && !saveError) persist().catch(() => {}); }, 2500);
document.addEventListener('DOMContentLoaded', () => boot().catch(() => showLogin()));

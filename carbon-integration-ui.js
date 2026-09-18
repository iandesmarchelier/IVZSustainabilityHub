/* Real IVZ Carbon integration: connect via token, map sites to locations, sync GHG measures. */
let carbonLink = {connected: false};

function carbonSourceBadge(locId) {
  if (carbonLink.connected && Object.values(carbonLink.siteMap || {}).includes(locId)) {
    return '<span class="badge teal"><i data-lucide="leaf" style="width:12px;height:12px"></i> Fuente: ' + CONFIG.CARBON_APP + '</span>';
  }
  return '';
}

async function loadCarbonLink() {
  try { carbonLink = await api('integrations/carbon'); }
  catch (e) { carbonLink = {connected: false}; }
  const entry = appState.integrations.find(i => i.id === 'INT-IVZC');
  if (entry) {
    entry.status = carbonLink.connected ? 'Connected' : 'Available';
    entry.lastSync = carbonLink.lastSync ? new Date(carbonLink.lastSync).toLocaleString('es-AR') : '—';
    entry.records = carbonLink.lastCount || 0;
  }
  if (ui.route === 'integraciones' || ui.route === 'emisiones') render();
}

const originalBoot = boot;
boot = async function() { await originalBoot(); await loadCarbonLink(); };

const originalOpenIntegration = openIntegration;
openIntegration = function(id) {
  if (id !== 'INT-IVZC') { originalOpenIntegration(id); return; }
  openCarbonIntegrationModal();
};

function openCarbonIntegrationModal() {
  if (!carbonLink.connected) {
    modal({title: 'IVZ Carbon', icon: 'leaf', size: 'mid',
      body: '<p style="margin-top:0">Pegá acá el token que generaste en IVZ Carbon (Integraciones → IVZ Sustainability Hub → Generar token) para que las emisiones de GEI de las ubicaciones que mapees se completen automáticamente, sin cargar Excel.</p>' +
        '<div class="field"><label>Token</label><input class="inp" id="carbon-token" placeholder="ivzc_…" style="width:100%"></div>',
      footer: '<button class="btn" data-close>Cancelar</button><button class="btn pri" id="carbon-connect">Conectar</button>',
      onMount: w => {
        w.querySelector('#carbon-connect').onclick = async e => {
          const button = e.currentTarget, token = w.querySelector('#carbon-token').value.trim();
          if (!token) { toast('Pegá un token válido.', 'warn'); return; }
          button.disabled = true;
          try { await api('integrations/carbon/connect', 'POST', {token}); closeModal(); await loadCarbonLink(); openCarbonIntegrationModal(); }
          catch (err) { toast(err.message, 'bad'); button.disabled = false; }
        };
      }});
    return;
  }
  const locations = appState.masterData.locations.filter(l => l.operable);
  const rows = carbonLink.sites.map(s => {
    const current = carbonLink.siteMap[s.id] || '';
    return '<tr><td>' + esc(s.name) + '<div class="muted" style="font-size:11px">' + esc(s.country || '') + '</div></td>' +
      '<td><select class="sel" data-site="' + esc(s.id) + '" style="width:100%"><option value="">Sin mapear</option>' +
      locations.map(l => '<option value="' + esc(l.id) + '"' + (current === l.id ? ' selected' : '') + '>' + esc(l.name) + '</option>').join('') +
      '</select></td></tr>';
  }).join('');
  modal({title: 'IVZ Carbon', icon: 'leaf', size: 'mid',
    badge: '<span class="badge ok"><span class="dot"></span>Connected</span>',
    body: '<div class="ai-strip"><i data-lucide="zap" style="width:18px;height:18px;color:var(--accent)"></i>' +
      '<div style="font-size:12.5px">Al sincronizar, las emisiones (Alcance 1/2/3) de cada ubicación mapeada se reemplazan por las calculadas en IVZ Carbon para ese sitio y año. El resto de los indicadores sigue siendo manual.</div></div>' +
      '<dl class="kv"><dt>Última sincronización</dt><dd>' + (carbonLink.lastSync ? new Date(carbonLink.lastSync).toLocaleString('es-AR') : 'Nunca') + '</dd>' +
      '<dt>Registros publicados</dt><dd>' + (carbonLink.lastCount ? fmt(carbonLink.lastCount) : '—') + '</dd></dl>' +
      (carbonLink.sites.length
        ? '<table class="tbl" style="margin-top:10px"><thead><tr><th>Sitio en IVZ Carbon</th><th>Ubicación en el Hub</th></tr></thead><tbody>' + rows + '</tbody></table>'
        : '<p class="muted">Todavía no hay sitios cargados en IVZ Carbon.</p>') +
      (locations.length ? '' : '<p class="muted" style="margin-top:8px">No hay ubicaciones operables en Datos Maestros para mapear.</p>'),
    footer: '<button class="btn" id="carbon-disconnect">Desconectar</button><button class="btn" data-close>Cerrar</button>' +
      '<button class="btn" id="carbon-save-map">Guardar mapeo</button>' +
      '<button class="btn pri" id="carbon-sync"><i data-lucide="refresh-cw"></i> Sincronizar ahora</button>',
    onMount: w => {
      w.querySelector('#carbon-disconnect').onclick = async () => {
        try { await api('integrations/carbon', 'DELETE'); closeModal(); await loadCarbonLink(); toast('IVZ Carbon desconectado.'); }
        catch (e) { toast(e.message, 'bad'); }
      };
      w.querySelector('#carbon-save-map').onclick = async e => {
        const button = e.currentTarget; button.disabled = true;
        const siteMap = {};
        w.querySelectorAll('[data-site]').forEach(sel => { if (sel.value) siteMap[sel.dataset.site] = sel.value; });
        try { const result = await api('integrations/carbon/mapping', 'PUT', {siteMap}); carbonLink.siteMap = result.siteMap; toast('Mapeo guardado.'); }
        catch (e) { toast(e.message, 'bad'); }
        finally { button.disabled = false; }
      };
      w.querySelector('#carbon-sync').onclick = async e => {
        const button = e.currentTarget, original = button.innerHTML;
        button.disabled = true; button.innerHTML = '<i data-lucide="loader-2" class="spin" style="width:14px;height:14px"></i> Sincronizando…'; icons();
        try {
          const result = await api('integrations/carbon/sync', 'POST');
          // The sync wrote directly to the server-side state; pull it back down so the
          // in-memory appState (and the revision/autosave bookkeeping in bridge.js) catch up.
          const fresh = await api('state');
          if (fresh.state) {
            appState.measures = fresh.state.measures;
            serverRevision = fresh.revision;
            savedState = JSON.stringify(appState);
          }
          await loadCarbonLink(); closeModal(); destroyCharts(); render();
          toast('Sincronizado: ' + fmt(result.lastCount) + ' registros.');
        } catch (err) { toast(err.message, 'bad'); button.disabled = false; button.innerHTML = original; icons(); }
      };
    }});
}

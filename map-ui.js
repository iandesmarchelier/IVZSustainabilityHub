/* Mapa ESG: globo 3D con el valor y el estado de cada indicador cuantitativo
   en cada ubicación operativa. Usa el motor de cálculo del Hub (computeMetric)
   y los objetivos cargados; nada se calcula aparte. */

const GM_STATUS = {
  "On Track": { c: "#4ADE80", l: "En trayectoria", b: "ok" },
  "At Risk": { c: "#F2C94C", l: "En riesgo", b: "warn" },
  "Off Track": { c: "#FF5A4E", l: "Fuera de trayectoria", b: "bad" },
  none: { c: "#A7BFB1", l: "Sin objetivo aplicable", b: "" }
};
const GM_TREND = {
  better: { c: "#4ADE80", l: "Mejora interanual", b: "ok" },
  flat: { c: "#F2C94C", l: "Estable (±2%)", b: "warn" },
  worse: { c: "#FF5A4E", l: "Empeora interanual", b: "bad" },
  none: { c: "#A7BFB1", l: "Sin dato del año anterior", b: "" }
};
const GM_MODES = [["estado", "Estado vs objetivo"], ["variacion", "Variación interanual"], ["valor", "Magnitud"]];

(function injectMapCSS() {
  const s = document.createElement("style");
  s.textContent = `
.gm-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.gm-wrap{display:grid;grid-template-columns:minmax(0,1fr) 390px;overflow:hidden;padding:0}
.gm-stage{position:relative;min-height:600px;background:#081a10}
.gm-globe{position:absolute;inset:0}
.gm-tabs{position:absolute;left:16px;top:16px;z-index:5;display:flex;border:1px solid rgba(190,230,205,.22);border-radius:8px;overflow:hidden;background:rgba(6,18,11,.7)}
.gm-tabs button{border:0;background:none;color:#CFE3D6;font:600 11.5px/1 Inter,system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase;padding:10px 14px;cursor:pointer}
.gm-tabs button+button{border-left:1px solid rgba(190,230,205,.14)}
.gm-tabs button.on{background:#D7EEDD;color:#15271E}
.gm-tabs button:hover:not(.on){background:rgba(60,120,85,.35)}
.gm-legend{position:absolute;left:16px;bottom:16px;z-index:5;background:rgba(6,18,11,.78);border:1px solid rgba(190,230,205,.18);border-radius:8px;padding:9px 11px;color:#CFE3D6;font-size:11.5px;display:grid;gap:5px;max-width:calc(100% - 90px)}
.gm-legend .row{display:flex;align-items:center;gap:7px;white-space:nowrap}
.gm-legend i{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none}
.gm-legend .ramp{height:7px;width:150px;border-radius:4px;background:linear-gradient(90deg,#3DDC84,#F2C94C 50%,#FF7A3A 78%,#FF3B30)}
.gm-legend .muted{color:#8FAA99}
.gm-pickbar{position:absolute;left:50%;top:64px;transform:translateX(-50%);z-index:6;background:#F2D14B;color:#15271E;font-weight:600;font-size:12.5px;padding:8px 12px;border-radius:999px;display:flex;gap:10px;align-items:center;box-shadow:0 6px 20px rgba(0,0,0,.3)}
.gm-pickbar button{border:0;background:rgba(21,39,30,.12);border-radius:999px;padding:3px 10px;font-weight:600;cursor:pointer}
.gm-side{padding:22px 24px;display:flex;flex-direction:column;gap:16px;background:#FCFDFB;border-left:1px solid var(--line);min-width:0}
.gm-eyebrow{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--accent);font-weight:700}
.gm-big{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.gm-big b{font-size:34px;line-height:1.05;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.gm-big span{color:var(--ink-2);font-weight:600}
.gm-chips{display:flex;gap:6px;flex-wrap:wrap}
.gm-list{display:flex;flex-direction:column;border-top:1px solid var(--line)}
.gm-row{display:grid;grid-template-columns:24px minmax(0,1fr) 88px auto;align-items:center;gap:10px;padding:9px 6px;border-bottom:1px solid var(--line);cursor:pointer;border-radius:6px}
.gm-row:hover{background:var(--accent-soft)}
.gm-row.on{background:#FFF7D6}
.gm-row .n{font:600 11px JetBrains Mono,monospace;color:var(--ink-3)}
.gm-row .nm{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}
.gm-row .nm small{display:block;color:var(--ink-3);font-size:11px}
.gm-row .bar{height:5px;border-radius:3px;background:var(--line);position:relative;overflow:hidden}
.gm-row .bar i{position:absolute;left:0;top:0;bottom:0;border-radius:3px}
.gm-row .v{font-weight:700;font-variant-numeric:tabular-nums;font-size:12.5px;text-align:right;white-space:nowrap}
.gm-row .v small{display:block;font-weight:500;color:var(--ink-3);font-size:10.5px}
.gm-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:1px}
.gm-note{border-left:3px solid var(--accent);background:var(--accent-soft);border-radius:0 8px 8px 0;padding:12px 14px;font-size:12.5px;color:var(--ink-2)}
.gm-note h4{margin:0 0 5px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.gm-note p{margin:0 0 4px}
.gm-sel{border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:var(--surface);font-size:12.5px}
.gm-sel .t{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.gm-sel .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;color:var(--ink-2)}
.gm-sel .kv b{color:var(--ink);font-variant-numeric:tabular-nums;text-align:right}
.gm-coords td{padding:6px 8px;vertical-align:middle}
.gm-coords input{width:100px}
@media(max-width:1200px){.gm-wrap{grid-template-columns:1fr}.gm-side{border-left:0;border-top:1px solid var(--line)}}
@media(max-width:560px){.gm-row{grid-template-columns:20px minmax(0,1fr) 36px auto;gap:7px}.gm-side{padding:16px 14px}}
@media(max-width:700px){.gm-stage{min-height:420px}.gm-tabs button{padding:8px 9px;font-size:10.5px}.gm-legend{display:none}}`;
  document.head.appendChild(s);
})();

const gmFmt = (v, unit) => unit === "%" ? fmt(v, 1) : fmtSmart(v, unit);
const gmNum = v => (v === null || v === undefined || v === "" || !isFinite(+v)) ? null : +v;

/* Coordenadas de una ubicación: dato cargado → ciudad del nombre → país */
function gmGeo(l) {
  const la = gmNum(l.lat), lo = gmNum(l.lon);
  if (la !== null && lo !== null && Math.abs(la) <= 90 && Math.abs(lo) <= 180) return { lat: la, lon: lo, src: "dato" };
  if (!window.IVZGlobe) return null;
  const g = IVZGlobe.geocode([l.city, l.name].filter(Boolean).join(" "), l.country);
  if (g && g.kind === "city") return { lat: g.lat, lon: g.lon, src: "ciudad", guess: g.name };
  const c = IVZGlobe.country(l.country) || IVZGlobe.geocode(l.countryName || "");
  if (c) return { lat: c.lat, lon: c.lon, src: "pais", guess: c.name };
  return null;
}
const GM_SRC = { dato: "Coordenadas cargadas", ciudad: "Estimada por la ciudad del nombre", pais: "Aproximada al centro del país" };

/* Objetivo aplicable a una ubicación: el propio de la ubicación o el del nodo más cercano que la contiene */
function gmTargetFor(metricId, locId) {
  const ts = appState.targets.filter(t => t.metricId === metricId);
  let best = null, bestSize = Infinity;
  ts.forEach(t => {
    const sc = scopeLocs(appState, t.loc || "GRP");
    if (!sc.includes(locId)) return;
    const size = (t.loc || "GRP") === locId ? 0 : sc.length;
    if (size < bestSize) { best = t; bestSize = size; }
  });
  return best;
}
/* Estado de la ubicación frente al objetivo. Si el objetivo es de un nodo superior,
   se aplica a la ubicación el mismo cambio relativo (métricas absolutas) o el mismo
   valor meta (porcentajes, promedios, intensidades). */
function gmTargetStatus(m, l, t) {
  if (!t) return null;
  const year = +filters.year, o = { period: "FY", s2: filters.s2, entity: filters.entity, locs: [l.id] };
  const cur = computeMetric(appState, m.id, Object.assign({ year }, o));
  const base = computeMetric(appState, m.id, Object.assign({ year: t.baseYear }, o));
  if (cur === null || base === null) return null;
  const intensive = m.unit === "%" || ["AVG", "RATIO", "SHARE"].includes(m.agg) || ["share", "formula"].includes(m.from);
  let tgt;
  if ((t.loc || "GRP") === l.id || intensive) tgt = t.targetValue;
  else { const gb = resolveBaseValue(t); if (!gb) return null; tgt = base * t.targetValue / gb; }
  const span = tgt - base, yrs = t.targetYear - t.baseYear;
  let progress = span === 0 ? (cur === tgt ? 100 : 0) : (cur - base) / span * 100;
  progress = Math.max(-40, Math.min(140, progress));
  const expected = yrs > 0 ? Math.max(0, Math.min(100, (year - t.baseYear) / yrs * 100)) : 100;
  let status = "On Track";
  if (progress < expected - 18) status = "Off Track"; else if (progress < expected - 5) status = "At Risk";
  return { status, progress, expected, tgt, base, cur, target: t, inherited: (t.loc || "GRP") !== l.id, intensive };
}

function gmRows(m) {
  const year = +filters.year, o = { period: filters.period, s2: filters.s2, entity: filters.entity };
  const locs = appState.masterData.locations.filter(l => l.operable && (filters.entity === "ALL" || l.entity === filters.entity));
  return locs.map(l => {
    const v = computeMetric(appState, m.id, Object.assign({ year, locs: [l.id] }, o));
    const prev = computeMetric(appState, m.id, Object.assign({ year: year - 1, locs: [l.id] }, o));
    const yoy = v !== null && prev !== null && prev !== 0 ? (v - prev) / Math.abs(prev) * 100 : null;
    const ts = gmTargetStatus(m, l, gmTargetFor(m.id, l.id));
    let trend = "none";
    if (yoy !== null) trend = Math.abs(yoy) < 2 ? "flat" : ((m.lowerIsBetter ? yoy < 0 : yoy > 0) ? "better" : "worse");
    return { l, geo: gmGeo(l), v, prev, yoy, ts, trend };
  });
}

function viewMapa(el) {
  if (ui.globe) { ui.dyn.gmView = ui.globe.getView(); ui.globe.destroy(); ui.globe = null; }
  const avail = appState.metrics.filter(m => filters.area === "ALL" || m.area === filters.area);
  if (!ui.dyn.gmMetric || !avail.find(m => m.id === ui.dyn.gmMetric)) ui.dyn.gmMetric = (avail.find(m => m.id === "ENV-GHG-TOT") || avail[0] || {}).id;
  const m = appState.metrics.find(x => x.id === ui.dyn.gmMetric);
  if (!m) { el.innerHTML = '<h1 class="page-title">Mapa ESG</h1>' + emptyState("No hay indicadores cuantitativos para el área seleccionada."); return; }
  const hasTargets = appState.targets.some(t => t.metricId === m.id);
  if (!ui.dyn.gmMode || (ui.dyn.gmMode === "estado" && !hasTargets && !ui.dyn.gmModeUser)) ui.dyn.gmMode = hasTargets ? "estado" : "variacion";
  const mode = ui.dyn.gmMode;
  const rows = gmRows(m);
  const withV = rows.filter(r => r.v !== null);
  const maxAbs = Math.max(0, ...withV.map(r => Math.abs(r.v)));
  const ranked = rows.slice().sort((a, b) => (b.v === null ? -Infinity : b.v) - (a.v === null ? -Infinity : a.v));
  const colorOf = r => {
    if (mode === "estado") return (r.ts ? GM_STATUS[r.ts.status] : GM_STATUS.none).c;
    if (mode === "variacion") return GM_TREND[r.trend].c;
    const t = maxAbs ? Math.abs(r.v || 0) / maxAbs : 0;
    return IVZGlobe.ramp(m.lowerIsBetter ? t : 1 - t);
  };
  const statusOf = r => mode === "estado" ? (r.ts ? GM_STATUS[r.ts.status] : GM_STATUS.none) : mode === "variacion" ? GM_TREND[r.trend] : null;
  const total = getMetricValue(m.id, { loc: "GRP" }), yoyTot = calculateYoY(m.id, { loc: "GRP" });
  const good = yoyTot === null ? "flat" : ((m.lowerIsBetter ? yoyTot < 0 : yoyTot > 0) ? "good" : "bad");
  if (ui.dyn.gmSel && !rows.some(r => r.l.id === ui.dyn.gmSel)) ui.dyn.gmSel = null;
  const noGeo = rows.filter(r => !r.geo), approx = rows.filter(r => r.geo && r.geo.src !== "dato");
  const counts = {};
  rows.filter(r => r.v !== null).forEach(r => { const s = statusOf(r); if (s) counts[s.l] = (counts[s.l] || 0) + 1; });
  const aggName = { SUM: "Suma del perímetro", AVG: "Promedio del perímetro", LAST: "Último valor informado", RATIO: "Indicador del perímetro", SHARE: "Participación del perímetro", MIN: "Mínimo", MAX: "Máximo" }[m.agg] || "Valor del perímetro";
  const periodTxt = filters.period === "FY" ? filters.year : filters.year + " · " + filters.period;

  el.innerHTML =
    '<div class="flexrow" style="margin-bottom:14px"><div><h1 class="page-title">Mapa ESG</h1>' +
    '<p class="page-sub">Cada ubicación operativa sobre el globo: la altura del pin muestra la magnitud del indicador y el color, su estado. Arrastrá para girar, Ctrl + rueda para acercar y hacé clic en un pin para ver el detalle.</p></div></div>' +
    '<div class="card" style="margin-bottom:16px"><div class="card-b gm-bar">' +
    '<span class="flabel muted">Indicador cuantitativo</span><select class="sel" id="gm-metric" style="min-width:300px;max-width:100%">' +
    ["Environmental", "Social", "Governance", "Economic"].map(a => {
      const g = avail.filter(x => x.area === a); if (!g.length) return "";
      return '<optgroup label="' + a + '">' + g.map(x => '<option value="' + x.id + '"' + (x.id === m.id ? " selected" : "") + ">" + esc(x.name) + " (" + esc(x.unit) + ")</option>").join("") + "</optgroup>";
    }).join("") + "</select>" +
    '<span class="badge accent">' + esc(m.area) + " · " + esc(m.topic || "") + "</span>" +
    (hasTargets ? '<span class="badge ok"><i data-lucide="target" style="width:12px;height:12px"></i> Con objetivo</span>' : '<span class="badge">Sin objetivo cargado</span>') +
    '<button class="btn sm right" id="gm-coords"><i data-lucide="map-pin"></i> Coordenadas de ubicaciones</button>' +
    "</div></div>" +
    '<div class="card gm-wrap"><div class="gm-stage">' +
    '<div class="gm-tabs" role="tablist">' + GM_MODES.map(([k, n]) => '<button role="tab" aria-selected="' + (k === mode) + '" data-gm-mode="' + k + '" class="' + (k === mode ? "on" : "") + '">' + n + "</button>").join("") + "</div>" +
    '<div class="gm-globe" id="gm-globe"></div>' +
    '<div class="gm-legend">' + gmLegend(mode, m) + "</div>" +
    "</div>" +
    '<div class="gm-side" id="gm-side"></div>' +
    "</div></div>";

  $("#gm-metric").onchange = e => { ui.dyn.gmMetric = e.target.value; ui.dyn.gmModeUser = false; render(); };
  $$("[data-gm-mode]").forEach(b => b.onclick = () => { ui.dyn.gmMode = b.dataset.gmMode; ui.dyn.gmModeUser = true; render(); });
  $("#gm-coords").onclick = openCoordsModal;
  ui.dyn._gm = { m, rows, mode, ranked, maxAbs, colorOf, statusOf, total, yoyTot, good, counts, aggName, periodTxt, noGeo, approx };
  ui.dyn.gmPick = null;
  gmRenderSide();

  const geoRows = rows.filter(r => r.geo);
  /* ubicaciones con la misma coordenada estimada: se separan en un círculo pequeño */
  const seen = {};
  geoRows.forEach(r => { const k = r.geo.lat.toFixed(2) + "," + r.geo.lon.toFixed(2); (seen[k] = seen[k] || []).push(r); });
  Object.values(seen).forEach(g => { if (g.length > 1) g.forEach((r, i) => { const a = i / g.length * Math.PI * 2; r.pos = [r.geo.lat + 0.9 * Math.sin(a), r.geo.lon + 0.9 * Math.cos(a) / Math.max(0.3, Math.cos(r.geo.lat * Math.PI / 180))]; }); });
  const points = geoRows.map(r => ({
    id: r.l.id, lat: (r.pos || [r.geo.lat])[0], lon: (r.pos || [0, r.geo.lon])[1],
    h: r.v === null ? 0 : Math.max(0.05, maxAbs ? Math.abs(r.v) / maxAbs : 0),
    color: r.v === null ? "#6F8A7A" : colorOf(r), selColor: r.v === null ? "#9FB8AA" : colorOf(r),
    size: r.v === null ? 3.5 : 5, label: r.l.name, pulse: mode !== "valor" && r.v !== null && (r.ts ? r.ts.status === "Off Track" : mode === "variacion" && r.trend === "worse"), row: r
  }));

  const host = $("#gm-globe");
  const g = ui.globe = IVZGlobe.create(host, {
    label: "Globo con " + m.name + " por ubicación",
    tooltip: it => it.row ? gmTip(it.row, m, statusOf(it.row)) : "",
    onClick: it => { if (it && it.row) { if (it.row.l.id !== ui.dyn.gmSel) gmSelect(it.row.l.id, false); } else if (ui.dyn.gmSel) gmSelect(null); },
    onPick: ll => {
      const l = appState.masterData.locations.find(x => x.id === ui.dyn.gmPick);
      if (l) { l.lat = +ll.lat.toFixed(4); l.lon = +ll.lon.toFixed(4); audit("Master data updated", "Coordenadas de " + l.name + ": " + l.lat + ", " + l.lon, "map-pin"); toast("Ubicación actualizada: " + esc(l.name)); ui.dyn.gmSel = l.id; }
      ui.dyn.gmPick = null; render();
    }
  });
  g.setData({ points });
  if (!points.length) g.setEmpty("Todavía no hay ubicaciones operativas con coordenadas. Cargalas desde «Coordenadas de ubicaciones».");
  else if (!withV.length) g.setEmpty("Sin datos de «" + m.name + "» para " + periodTxt + ".");
  if (ui.dyn.gmView) { g.fit(points.map(p => [p.lat, p.lon]), { home: true, instant: true, maxZoom: 1.35, tilt: 14 }); g.setView(ui.dyn.gmView); }
  else g.fit(points.map(p => [p.lat, p.lon]), { home: true, instant: true, maxZoom: 1.35, tilt: 14 });
  if (ui.dyn.gmSel) g.select(ui.dyn.gmSel, false);
}

/* Panel lateral: se redibuja solo, sin recrear el globo, al seleccionar una ubicación */
function gmRenderSide() {
  const S = ui.dyn._gm, box = $("#gm-side"); if (!S || !box) return;
  const { m, rows, mode, ranked, maxAbs, colorOf, statusOf, total, yoyTot, good, counts, aggName, periodTxt, noGeo, approx } = S;
  const sel = rows.find(r => r.l.id === ui.dyn.gmSel) || null;
  box.innerHTML =
    '<div><div class="gm-eyebrow">' + esc(m.name) + " · " + esc(periodTxt) + "</div>" +
    '<div class="gm-big" style="margin-top:6px"><b>' + gmFmt(total, m.unit) + "</b><span>" + esc(unitSmart(total, m.unit)) + "</span></div>" +
    '<div class="muted" style="font-size:12px;margin-top:3px">' + aggName + ' · <span class="delta ' + good + '">' + pct(yoyTot) + " interanual</span></div></div>" +
    (Object.keys(counts).length ? '<div class="gm-chips">' + Object.entries(counts).map(([k, n]) => { const s = Object.values(mode === "estado" ? GM_STATUS : GM_TREND).find(x => x.l === k); return '<span class="badge ' + (s ? s.b : "") + '"><span class="gm-dot" style="background:' + (s ? s.c : "#aaa") + ';margin-right:0"></span>' + n + " " + esc(k.toLowerCase()) + "</span>"; }).join("") + "</div>" : "") +
    (sel ? gmSelCard(sel, m, colorOf(sel), statusOf(sel)) : "") +
    '<div class="gm-list" id="gm-list">' + (ranked.length ? ranked.map((r, i) => {
      const w = maxAbs && r.v !== null ? Math.max(2, Math.abs(r.v) / maxAbs * 100) : 0;
      return '<div class="gm-row' + (r.l.id === ui.dyn.gmSel ? " on" : "") + '" data-gm-loc="' + esc(r.l.id) + '" title="' + esc(r.l.name) + '">' +
        '<span class="n">' + String(i + 1).padStart(2, "0") + "</span>" +
        '<span class="nm"><span class="gm-dot" style="background:' + (r.v === null ? "#CFD7D1" : colorOf(r)) + '"></span>' + esc(r.l.name) + "<small>" + esc(r.l.countryName || "—") + (r.geo ? "" : " · sin ubicación en el mapa") + "</small></span>" +
        '<span class="bar"><i style="width:' + w + "%;background:" + (r.v === null ? "transparent" : colorOf(r)) + '"></i></span>' +
        '<span class="v">' + (r.v === null ? '<span class="muted">sin dato</span>' : gmFmt(r.v, m.unit) + ' <small>' + esc(unitSmart(r.v, m.unit)) + (r.yoy !== null ? " · " + pct(r.yoy, 0) : "") + "</small>") + "</span></div>";
    }).join("") : '<p class="muted">No hay ubicaciones operativas en el perímetro.</p>') + "</div>" +
    gmInsight(m, rows, mode) +
    ((noGeo.length || approx.length) ? '<p class="muted" style="font-size:11.5px;margin:0">' +
      (approx.length ? approx.length + (approx.length === 1 ? " ubicación" : " ubicaciones") + " con coordenadas estimadas. " : "") +
      (noGeo.length ? noGeo.length + " sin ubicación en el mapa. " : "") +
      '<a href="#" id="gm-coords-2">Cargar coordenadas exactas</a></p>' : "") +
    "";
  $$("[data-gm-loc]", box).forEach(r => r.onclick = () => gmSelect(r.dataset.gmLoc, true));
  if ($("#gm-coords-2")) $("#gm-coords-2").onclick = e => { e.preventDefault(); openCoordsModal(); };
  if ($("#gm-relocate")) $("#gm-relocate").onclick = () => gmStartPick(ui.dyn.gmSel);
  icons();
}

function gmSelect(id, fly) {
  ui.dyn.gmSel = id && id !== ui.dyn.gmSel ? id : null;
  if (ui.globe) ui.globe.select(ui.dyn.gmSel, !!fly);
  gmRenderSide();
}
function gmStartPick(id) {
  if (!id || !ui.globe) return;
  ui.dyn.gmPick = id;
  const bar = document.createElement("div"); bar.className = "gm-pickbar"; bar.id = "gm-pickbar";
  bar.innerHTML = "Hacé clic en el globo para ubicar «" + esc(locName(id)) + '»<button type="button">Cancelar</button>';
  bar.querySelector("button").onclick = () => { ui.dyn.gmPick = null; bar.remove(); ui.globe && ui.globe.setPickMode(false); };
  const old = $("#gm-pickbar"); if (old) old.remove();
  $(".gm-stage").appendChild(bar);
  ui.globe.setPickMode(true);
}

function gmLegend(mode, m) {
  const row = (c, l) => '<div class="row"><i style="background:' + c + '"></i>' + esc(l) + "</div>";
  if (mode === "estado") return Object.values(GM_STATUS).map(s => row(s.c, s.l)).join("") + '<div class="row muted">Altura del pin = magnitud · parpadeo = fuera de trayectoria</div>';
  if (mode === "variacion") return Object.values(GM_TREND).map(s => row(s.c, s.l)).join("") + '<div class="row muted">' + (m.lowerIsBetter ? "Para este indicador, bajar es mejorar" : "Para este indicador, subir es mejorar") + "</div>";
  return '<div class="row"><span>' + (m.lowerIsBetter ? "Menor" : "Mayor") + '</span><span class="ramp"></span><span>' + (m.lowerIsBetter ? "Mayor" : "Menor") + "</span></div>" +
    '<div class="row muted">Color y altura según el valor relativo entre ubicaciones</div>';
}

function gmTip(r, m, s) {
  const v = r.v === null ? "sin dato" : gmFmt(r.v, m.unit) + " " + unitSmart(r.v, m.unit);
  return "<b>" + esc(r.l.name) + '</b><div class="d">' + esc([r.l.type, r.l.countryName].filter(Boolean).join(" · ")) + "</div>" +
    '<div style="margin-top:5px">' + esc(m.name) + ": <b>" + v + "</b></div>" +
    (r.yoy !== null ? '<div class="d">Interanual: ' + pct(r.yoy) + "</div>" : "") +
    (r.ts ? '<div style="margin-top:4px"><span style="color:' + GM_STATUS[r.ts.status].c + '">●</span> ' + GM_STATUS[r.ts.status].l + " · avance " + fmt(r.ts.progress, 0) + "% (esperado " + fmt(r.ts.expected, 0) + "%)</div>" : (s && m ? "" : "")) +
    (r.geo && r.geo.src !== "dato" ? '<div class="d" style="margin-top:4px">' + GM_SRC[r.geo.src] + (r.geo.guess ? ": " + esc(r.geo.guess) : "") + "</div>" : "");
}

function gmSelCard(r, m, col, s) {
  const kv = (k, v) => "<span>" + k + "</span><b>" + v + "</b>";
  const u = x => x === null || x === undefined ? "—" : gmFmt(x, m.unit) + " " + esc(unitSmart(x, m.unit));
  return '<div class="gm-sel"><div class="t"><span class="gm-dot" style="background:' + col + '"></span><b>' + esc(r.l.name) + "</b>" +
    (s ? '<span class="badge ' + s.b + ' right">' + esc(s.l) + "</span>" : "") + "</div>" +
    '<div class="kv">' + kv(esc(filters.year), u(r.v)) + kv(esc(String(filters.year - 1)), u(r.prev)) + kv("Interanual", pct(r.yoy)) +
    (r.ts ? kv("Meta " + r.ts.target.targetYear + (r.ts.inherited ? (r.ts.intensive ? " (del grupo)" : " (proporcional)") : ""), u(r.ts.tgt)) + kv("Avance / esperado", fmt(r.ts.progress, 0) + "% / " + fmt(r.ts.expected, 0) + "%") : "") +
    kv("Ubicación", r.geo ? (r.geo.src === "dato" ? fmt(r.geo.lat, 3) + ", " + fmt(r.geo.lon, 3) : "estimada") : "sin coordenadas") + "</div>" +
    '<div class="flexrow" style="margin-top:9px;gap:6px"><button class="btn sm" id="gm-relocate"><i data-lucide="crosshair"></i> Ubicar en el globo</button>' +
    '<button class="btn sm" onclick="filters.loc=\'' + esc(r.l.id) + '\';go(\'desempeno\')"><i data-lucide="bar-chart-3"></i> Analizar</button></div></div>';
}

/* Lectura automática del mapa */
function gmInsight(m, rows, mode) {
  const w = rows.filter(r => r.v !== null);
  if (!w.length) return "";
  const lines = [];
  const top = w.slice().sort((a, b) => b.v - a.v)[0];
  const sum = w.reduce((a, r) => a + r.v, 0);
  if (["SUM", "LAST"].includes(m.agg) && sum > 0 && w.length > 1) lines.push("<b>" + esc(top.l.name) + "</b> concentra el <b>" + fmt(top.v / sum * 100, 0) + "%</b> del total entre las ubicaciones con dato.");
  else if (w.length > 1) { const lo = w.slice().sort((a, b) => a.v - b.v)[0]; lines.push("Rango entre ubicaciones: de <b>" + fmtSmart(lo.v, m.unit) + " " + esc(unitSmart(lo.v, m.unit)) + "</b> (" + esc(lo.l.name) + ") a <b>" + fmtSmart(top.v, m.unit) + " " + esc(unitSmart(top.v, m.unit)) + "</b> (" + esc(top.l.name) + ")."); }
  const off = w.filter(r => r.ts && r.ts.status === "Off Track"), risk = w.filter(r => r.ts && r.ts.status === "At Risk");
  if (off.length) lines.push("<b>" + off.length + "</b> fuera de trayectoria: " + off.map(r => esc(r.l.name)).join(", ") + ".");
  else if (risk.length) lines.push("<b>" + risk.length + "</b> en riesgo: " + risk.map(r => esc(r.l.name)).join(", ") + ".");
  else if (w.some(r => r.ts)) lines.push("Todas las ubicaciones con objetivo avanzan según la trayectoria esperada.");
  const worse = w.filter(r => r.trend === "worse").sort((a, b) => Math.abs(b.yoy) - Math.abs(a.yoy))[0];
  if (worse && mode !== "estado") lines.push("Mayor deterioro interanual: <b>" + esc(worse.l.name) + "</b> (" + pct(worse.yoy) + ").");
  const better = w.filter(r => r.trend === "better").sort((a, b) => Math.abs(b.yoy) - Math.abs(a.yoy))[0];
  if (better) lines.push("Mayor mejora interanual: <b>" + esc(better.l.name) + "</b> (" + pct(better.yoy) + ").");
  return '<div class="gm-note"><h4>Lectura del mapa</h4>' + lines.map(x => "<p>" + x + "</p>").join("") + "</div>";
}

function openCoordsModal() {
  const locs = appState.masterData.locations.filter(l => l.operable);
  modal({
    title: "Coordenadas de las ubicaciones", icon: "map-pin", wide: true,
    body: '<p class="muted" style="font-size:12.5px;margin:0 0 10px">Latitud y longitud en grados decimales (por ejemplo, -34.6037 y -58.3816). Sin coordenadas, el mapa estima la posición por la ciudad del nombre o por el país. También podés seleccionar una ubicación en el mapa y usar «Ubicar en el globo».</p>' +
      '<div class="tbl-wrap" style="max-height:52vh"><table class="tbl gm-coords"><thead><tr><th>Ubicación</th><th>Posición actual</th><th>Latitud</th><th>Longitud</th></tr></thead><tbody>' +
      locs.map(l => { const g = gmGeo(l); return "<tr><td><b>" + esc(l.name) + '</b><div class="muted mono" style="font-size:11px">' + esc(l.id) + "</div></td>" +
        '<td class="muted" style="font-size:12px">' + (g ? GM_SRC[g.src] + (g.guess ? ": " + esc(g.guess) : "") : "Sin ubicación") + "</td>" +
        '<td><input class="inp mono" data-lat="' + esc(l.id) + '" value="' + (gmNum(l.lat) !== null ? l.lat : "") + '" placeholder="' + (g ? g.lat.toFixed(4) : "") + '"></td>' +
        '<td><input class="inp mono" data-lon="' + esc(l.id) + '" value="' + (gmNum(l.lon) !== null ? l.lon : "") + '" placeholder="' + (g ? g.lon.toFixed(4) : "") + '"></td></tr>'; }).join("") +
      "</tbody></table></div>",
    footer: '<button class="btn" data-close>Cancelar</button><button class="btn pri" id="gm-save-coords">Guardar coordenadas</button>',
    onMount: w => $("#gm-save-coords", w).onclick = () => {
      let n = 0, bad = [];
      locs.forEach(l => {
        const a = $('[data-lat="' + CSS.escape(l.id) + '"]', w).value.trim().replace(",", "."), b = $('[data-lon="' + CSS.escape(l.id) + '"]', w).value.trim().replace(",", ".");
        if (!a && !b) { if (l.lat != null || l.lon != null) { delete l.lat; delete l.lon; n++; } return; }
        const la = parseFloat(a), lo = parseFloat(b);
        if (!(isFinite(la) && isFinite(lo) && Math.abs(la) <= 90 && Math.abs(lo) <= 180)) { bad.push(l.name); return; }
        if (l.lat !== la || l.lon !== lo) { l.lat = la; l.lon = lo; n++; }
      });
      if (bad.length) { toast("Coordenadas inválidas en: " + esc(bad.join(", ")) + ". Latitud entre -90 y 90, longitud entre -180 y 180.", "warn"); return; }
      if (n) audit("Master data updated", "Coordenadas actualizadas en " + n + " ubicaciones", "map-pin");
      closeModal(); toast(n ? "Coordenadas guardadas (" + n + ")." : "Sin cambios."); render();
    }
  });
}

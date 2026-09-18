/* General ESG reporting: original wizard, frozen figures and section editing. */
let selectedReportSection = null;

runAiGeneration = async function() {
  const request = {year: RW.year, scope: RW.scope, scopeKind: RW.scopeKind || 'L',
    s2: RW.s2, template: 'gen', sections: RW.sections.filter(s => s.on).map(s => s.id), useAI: !!RW.useAI};
  modal({title:'Generando reporte ESG', icon:'file-text', body:
    '<div class="flexrow" style="gap:12px;align-items:center"><i data-lucide="loader-2" class="spin" style="width:22px;height:22px;color:var(--accent);flex:none"></i>' +
    '<p id="report-progress" style="margin:0">Guardando datos del período…</p></div>'});
  try {
    await persist();
    const progress = document.getElementById('report-progress');
    if (progress) progress.textContent = request.useAI ? 'Calculando indicadores y generando el resumen ejecutivo con IA…' : 'Calculando indicadores y preparando las secciones y los gráficos…';
    const report = await api('reports','POST',request);
    appState.report = report; selectedReportSection = report.sections[0]?.id;
    await persist(); closeModal(); ui.tab.reportes = 'editor'; go('reportes');
  } catch(e) { closeModal(); toast(e.message,'bad'); drawReportWizard(); }
};

async function saveReport(approve = false) {
  if (document.activeElement?.isContentEditable) document.activeElement.blur();
  const body = JSON.parse(JSON.stringify(appState.report));
  body.meta.status = approve ? 'Approved' : 'Draft';
  const report = await api('reports/' + encodeURIComponent(body.id),'PUT',body);
  appState.report = report;
  await persist();
  return report;
}

renderReportEditor = function(el) {
  const report = appState.report;
  if (!report.sections.some(s => s.id === selectedReportSection)) selectedReportSection = report.sections[0]?.id;
  originalReportEditor(el);
  const strip = el.querySelector('.ai-strip div');
  if (strip) strip.textContent = report.meta.mode + '. Editá los textos y revisá el documento antes de aprobarlo.';
  el.querySelector('#r-dl').textContent = 'Descargar PDF';
  el.querySelector('#r-print').textContent = 'Imprimir';
  el.querySelector('#r-prev').textContent = 'Vista previa';
  el.querySelector('#r-regen').onclick = () => regenSection(selectedReportSection);
  el.querySelectorAll('[data-rsec]').forEach(button => {
    button.classList.toggle('on',button.dataset.rsec === selectedReportSection);
    button.onclick = () => {
      selectedReportSection = button.dataset.rsec;
      el.querySelectorAll('[data-rsec]').forEach(b => b.classList.toggle('on',b === button));
      document.getElementById('sec-' + selectedReportSection)?.scrollIntoView({behavior:'smooth',block:'start'});
    };
  });
  const group = el.querySelector('#r-regen').parentElement;
  for (const [label,approve] of [['Guardar borrador',false],['Aprobar reporte',true]]) {
    const button = document.createElement('button'); button.className = 'btn' + (approve ? ' pri' : ''); button.textContent = label;
    button.onclick = async () => {
      button.disabled = true;
      try {await saveReport(approve); render(); toast(approve ? 'Reporte aprobado.' : 'Borrador guardado.');}
      catch(e) {toast(e.message,'bad');} finally {button.disabled = false;}
    };
    group.appendChild(button);
  }
};

drawReportDoc = function() {
  const report = appState.report, doc = document.getElementById('rep-doc');
  const cell = v => v == null ? 'Sin datos' : fmt(v,1);
  function blockHTML(block,index,section) {
    if (block.type === 'h3') return '<h3>'+esc(block.text)+'</h3>';
    if (block.type === 'chart') return '<figure class="rep-fig"><div class="chart-box"><canvas id="'+esc(block.id)+'"></canvas></div><figcaption class="cap">Figura · '+esc(block.cap)+'</figcaption></figure>';
    if (block.type === 'table') {
      if (!block.rows?.length) return '<p class="muted">Sin objetivos configurados para el alcance y período.</p>';
      return '<div class="rep-fig" style="padding:0"><table class="tbl"><thead><tr><th>Objetivo</th><th>Base</th><th>Actual</th><th>Meta</th><th>Unidad</th><th>Estado</th></tr></thead><tbody>'+block.rows.map(t =>
        '<tr><td>'+esc(t.name)+'</td><td>'+cell(t.base)+'</td><td>'+cell(t.current)+'</td><td>'+cell(t.goal)+'</td><td>'+esc(t.unit)+'</td><td><span class="badge '+(t.status==='On Track'?'ok':t.status==='At Risk'?'warn':t.status==='Off Track'?'bad':'')+'">'+esc(t.status)+'</span></td></tr>').join('')+'</tbody></table></div>';
    }
    return '<p contenteditable="true" data-sec="'+esc(section.id)+'" data-blk="'+index+'">'+esc(block.text)+'</p>';
  }
  doc.innerHTML = '<div class="rep-cover"><div class="flexrow" style="margin-bottom:14px"><img class="rep-logo" src="'+CONFIG.LOGO+'" alt="Invenzis"><span class="muted">IVZ Sustainability Hub · Invenzis</span></div>'+
    '<h1>'+esc(report.meta.title)+'</h1><p class="muted">'+esc(report.meta.scopeName)+' · ejercicio '+report.meta.year+' · '+(report.meta.status==='Approved'?'aprobado':'borrador')+' generado el '+esc(new Date(report.meta.generated).toLocaleDateString('es-AR'))+'</p></div>'+
    '<div class="disclaim"><b>Aviso.</b> El reporte refleja la información disponible al momento de su generación. Los resultados deben ser revisados y validados antes de su publicación.</div>'+
    '<div class="rep-fig" style="background:#fff"><b>Contenido</b><ol>'+report.sections.map(s => '<li><a href="#sec-'+esc(s.id)+'">'+esc(s.title.replace(/^\d+\.\s*/,''))+'</a></li>').join('')+'</ol></div>'+
    report.sections.map(s => '<section id="sec-'+esc(s.id)+'"><h2>'+esc(s.title)+'</h2>'+s.blocks.map((b,i)=>blockHTML(b,i,s)).join('')+
      '<div class="no-print" style="margin-top:6px"><button class="btn sm" data-regen="'+esc(s.id)+'">Regenerar sección</button></div></section>').join('');
  doc.querySelectorAll('[contenteditable]').forEach(p => p.oninput = e => {
    const section = report.sections.find(s => s.id === e.target.dataset.sec);
    section.blocks[+e.target.dataset.blk].text = e.target.textContent;
    report.meta.status = 'Draft'; delete report.meta.approvedAt;
  });
  doc.querySelectorAll('[data-regen]').forEach(button => button.onclick = () => regenSection(button.dataset.regen));
  drawReportCharts(); icons();
};

drawReportCharts = function() {
  const colors = [PAL.accent,PAL.teal,PAL.accent3];
  appState.report.sections.forEach(s => s.blocks.filter(b => b.type === 'chart').forEach(b => {
    const canvas = document.getElementById(b.id);
    const values = (b.datasets || []).flatMap(d => d.data);
    if (!canvas) return;
    if (!values.some(v => v !== null && v !== undefined) || (b.kind === 'donut' && !values.some(v => v > 0))) {
      canvas.parentElement.innerHTML = '<p class="muted" style="padding:30px;text-align:center">'+(values.some(v => v === 0)?'Todos los valores son cero.':'Sin datos para este gráfico.')+'</p>';
      return;
    }
    const datasets = b.datasets.map((d,i) => ({...d,backgroundColor:b.kind==='horizontal'?(b.id==='rep-safety'?PAL.warn:PAL.teal):colors[i%colors.length]}));
    let cfg;
    if (b.kind === 'trajectory') cfg = lineCfg(b.labels,b.datasets.map((d,i)=>({...d,borderColor:colors[i],backgroundColor:colors[i]+'22',borderDash:i?[6,4]:[],fill:i===0,tension:0,spanGaps:false})),{animation:false,plugins:{legend:{display:true,position:'bottom'}}});
    else if (b.kind === 'donut') cfg = donutCfg(b.labels,b.datasets[0].data,colors);
    else if (b.kind === 'stack') cfg = {type:'bar',data:{labels:b.labels,datasets},options:baseOpts({animation:false,plugins:{legend:{display:true,position:'bottom'}},scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,beginAtZero:true}}})};
    else if (b.kind === 'objectives') {
      const labels = b.labels.map(label => {
        const lines = [''];
        for (const word of label.split(' ')) {
          if ((lines[lines.length-1]+' '+word).trim().length > 28 && lines[lines.length-1]) lines.push(word);
          else lines[lines.length-1] = (lines[lines.length-1]+' '+word).trim();
        }
        return lines;
      });
      canvas.parentElement.style.height = Math.max(230,labels.reduce((sum,l)=>sum+Math.max(62,l.length*15+12),0)+55)+'px';
      cfg = barCfg(labels,datasets,{animation:false},true);
      cfg.options.plugins.legend = {display:true,position:'bottom',labels:{boxWidth:10,font:{size:11}}};
      cfg.options.scales.x.suggestedMax = 100;
      cfg.options.scales.x.title = {display:true,text:'Avance hacia la meta (%)'};
    }
    else cfg = barCfg(b.labels,datasets,{animation:false},b.kind === 'horizontal');
    cfg.options.animation = false;
    mkChart(b.id,cfg);
  }));
};

regenSection = function(id) {
  if (!id) return;
  const section = appState.report.sections.find(s => s.id === id);
  if (!section) return;
  modal({title:'Regenerar '+section.title,body:'<p>'+ (appState.report.meta.useAI ? 'Se reescribirán los textos de esta sección con IA, a partir de los datos guardados en el reporte.' : 'Se restaurará el texto calculado de esta sección (este reporte se generó sin IA).') + ' Las ediciones de esta sección serán reemplazadas.</p>',
    footer:'<button class="btn" data-close>Cancelar</button><button class="btn pri" id="confirm-regenerate">Regenerar sección</button>',onMount:w => {
      w.querySelector('#confirm-regenerate').onclick = async e => {
        const button = e.currentTarget; const original = button.innerHTML;
        button.disabled = true; button.innerHTML = '<i data-lucide="loader-2" class="spin" style="width:14px;height:14px"></i> Regenerando…'; icons();
        try {
          await saveReport(false);
          const report = await api('reports/'+encodeURIComponent(appState.report.id)+'/sections/'+encodeURIComponent(id)+'/regenerate','POST',{});
          appState.report = report; await persist(); closeModal(); destroyCharts(); render(); toast('Sección regenerada.');
        } catch(error) {toast(error.message,'bad'); button.disabled = false; button.innerHTML = original;}
      };
    }});
};

downloadReport = async function() {
  try {
    await saveReport(appState.report.meta.status === 'Approved');
    const doc = document.getElementById('rep-doc');
    if (typeof html2pdf === 'undefined') {window.print(); return;}
    const buttons = doc.querySelectorAll('.no-print');
    buttons.forEach(b => b.style.display='none');
    doc.classList.add('exporting');
    try {
      await document.fonts.ready;
      Object.values(ui.charts).forEach(c => { c.resize(); c.update('none'); });
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      // Freeze charts as images at the final paper width before html2pdf clones/reflows
      // the document. Explicit CSS dimensions keep high-DPI buffers inside their frames.
      const copy = doc.cloneNode(true);
      copy.querySelectorAll('h2,h3').forEach(heading => {
        const next = heading.nextElementSibling;
        if (next && next.tagName === 'P') {
          const lead = document.createElement('div'); lead.className = 'report-section-lead';
          heading.before(lead); lead.append(heading,next);
        }
      });
      const originals = doc.querySelectorAll('canvas');
      copy.querySelectorAll('canvas').forEach((canvas,i) => {
        const source = originals[i], size = source.getBoundingClientRect();
        const img = document.createElement('img');
        img.src = source.toDataURL('image/png'); img.className = 'report-chart-image';
        img.style.width = size.width+'px'; img.style.height = size.height+'px';
        canvas.replaceWith(img);
      });
      await html2pdf().set({margin:12,filename:'IVZ_Sustainability_Report_'+appState.report.meta.year+'.pdf',
        image:{type:'jpeg',quality:.98},html2canvas:{scale:2,useCORS:true,scrollY:0},
        jsPDF:{unit:'mm',format:'a4',orientation:'portrait'},pagebreak:{mode:['css','legacy'],avoid:['.rep-fig','.report-section-lead','tr']}}).from(copy).save();
    } finally {
      buttons.forEach(b => b.style.display='');
      doc.classList.remove('exporting');
      drawReportCharts();
    }
  } catch(e) {toast(e.message,'bad');}
};

function openReportHistory() {
  api('reports').then(reports => modal({title: 'Reportes guardados', body: reports.length ? reports.map((r, i) =>
      '<p><button class="btn" data-history="' + i + '">' + esc(r.meta.title) + ' · ' + esc(r.meta.status) + '</button></p>').join('') : '<p>No hay reportes.</p>',
    footer: '<button class="btn" data-close>Cerrar</button>', onMount: w => {
      w.querySelectorAll('[data-history]').forEach(b => b.onclick = () => {appState.report = reports[+b.dataset.history]; closeModal(); ui.tab.reportes = 'editor'; go('reportes');});
    }})).catch(e => toast(e.message, 'bad'));
}

const originalReportList = viewReportes;
viewReportes = function(el) {
  originalReportList(el);
  if (ui.tab.reportes === 'editor') return;
  el.querySelectorAll('.card').forEach(card => {
    if (card.querySelector('h3')?.textContent === 'Frameworks de reporte') {
      card.querySelector('.card-b').innerHTML = '<b>Reporte ESG General</b><p class="muted">Estructura ambiental, social, de gobernanza y económica. Otros marcos se configurarán más adelante.</p>';
    }
  });
  const genButton = el.querySelector('#r-gen');
  if (genButton && !el.querySelector('#r-history')) {
    const historyButton = document.createElement('button');
    historyButton.className = 'btn'; historyButton.id = 'r-history'; historyButton.style.padding = '10px 18px';
    historyButton.innerHTML = '<i data-lucide="history"></i> Reportes guardados';
    historyButton.onclick = openReportHistory;
    genButton.before(historyButton);
  }
};

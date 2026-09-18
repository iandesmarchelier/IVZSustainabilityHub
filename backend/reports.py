"""General ESG report with server-calculated, frozen figures."""
import copy
import os
from datetime import datetime, timezone
import httpx
from .metrics import compute, facts, scope_locations

TITLES = dict(exec='1. Executive Summary', intro='2. Introduction / Reporting Scope',
    env='3. Environmental Performance', climate='3.1 Carbon footprint', energy='3.2 Energy',
    water='3.3 Water', waste='3.4 Waste', land='3.5 Land / Biodiversity',
    social='4. Social Performance', hs='4.1 Health & Safety', di='4.2 Diversity & Inclusion',
    emp='4.3 Employees', gov='5. Governance', eco='6. Economic Performance',
    targets='7. ESG Targets and Ambitions', impr='8. Improvement Opportunities')
PARENTS = {**dict.fromkeys(['climate','energy','water','waste','land'], 'env'), **dict.fromkeys(['hs','di','emp'], 'social')}


def number(v):
    return 'sin datos' if v is None else f'{v:,.1f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def selected_sections(request):
    selected = set(TITLES if request.get('sections') is None else request['sections'])
    if not selected or selected - TITLES.keys():
        raise ValueError('Seleccioná al menos una sección válida')
    return selected | {PARENTS[s] for s in selected if s in PARENTS}


async def ai_text(prompt):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post('https://generativelanguage.googleapis.com/v1beta/interactions',
            headers={'x-goog-api-key':os.environ['GEMINI_API_KEY']},
            json={'model':os.getenv('GEMINI_MODEL','gemini-3.8-flash'), 'input':prompt})
    r.raise_for_status()
    text = '\n'.join(c.get('text','') for step in r.json().get('steps',[]) if step.get('type') == 'model_output'
                     for c in step.get('content',[]) if c.get('type') == 'text')
    if not text.strip():
        raise RuntimeError('La IA no devolvió texto')
    return text


async def make_report(st, request, company):
    year, scope, s2 = request['year'], request.get('scope','ALL'), request.get('s2','Market-based')
    entity = scope if request.get('scopeKind') == 'E' else None
    selected = selected_sections(request)
    values = facts(st, year, scope, entity, s2)
    lookup = {v['id']:v for v in values}
    loc_ids = scope_locations(st, scope, entity)
    locations = [l for l in st['masterData']['locations'] if l['id'] in loc_ids and l.get('operable')]
    nodes = st['masterData']['legalEntities' if entity else 'locations']
    scope_name = company + ' (todas las ubicaciones)' if scope == 'ALL' else next((x['name'] for x in nodes if x['id'] == scope),scope)
    def amount(mid):
        v = lookup.get(mid,{})
        return 'sin datos' if v.get('value') is None else number(v['value']) + ' ' + v.get('unit','')
    def change(mid):
        v = lookup.get(mid,{})
        a,b = v.get('value'),v.get('previous')
        if a is None or b in (None,0):
            return 'sin variación interanual comparable'
        d = (a-b)/abs(b)*100
        return ('una reducción del ' if d < 0 else 'un aumento del ') + number(abs(d)) + '% respecto de ' + str(year-1)
    def qual(mid):
        rows = [r for r in st.get('qualitative',[]) if r.get('metricId') == mid and r.get('y') == year and r.get('loc') in loc_ids]
        return '; '.join(str(r['value']) for r in rows if r.get('value') not in (None,'')) or 'no informado para este alcance y período'
    def p(text):
        return {'type':'p','text':text}
    def chart(cid,cap,kind,labels,datasets):
        return {'type':'chart','id':'rep-'+cid,'cap':cap,'kind':kind,'labels':labels,'datasets':datasets}
    years = sorted({r['y'] for r in st['measures']+st['actuals'] if r['loc'] in loc_ids and r['y'] <= year})[-5:] or [year]
    mids = ['ENV-GHG-S1','ENV-GHG-S2M' if s2 == 'Market-based' else 'ENV-GHG-S2L','ENV-GHG-S3']
    series = [{'label':label,'data':[compute(st,mid,y,scope,entity,s2) for y in years]} for mid,label in zip(mids,['Scope 1','Scope 2','Scope 3'])]
    def locchart(mid,cid,caption,unit):
        rows = sorted([(l['name'],compute(st,mid,year,l['id'],None,s2)) for l in locations],key=lambda r:r[1] if r[1] is not None else -float('inf'),reverse=True)
        return chart(cid,caption,'horizontal',[r[0] for r in rows],[{'label':unit,'data':[r[1] for r in rows]}])
    targets = []
    for t in st['targets']:
        ts = t.get('loc') or 'ALL'
        # Older imports used GRP for "all locations", even without a GRP node.
        if ts == 'GRP' and not any(l['id'] == 'GRP' for l in st['masterData']['locations']):
            ts = 'ALL'
        if ts not in loc_ids and not (scope == 'ALL' and ts == 'ALL'):
            continue
        if entity and not scope_locations(st,ts).issubset(loc_ids):
            continue
        by,ty = t.get('baseYear',year),t.get('targetYear',year)
        base = compute(st,t['metricId'],by,ts,None,s2)
        if base is None:
            base = t.get('baseValue')
        cur,goal = compute(st,t['metricId'],year,ts,None,s2),t.get('targetValue')
        expected = min(100,max(0,(year-by)/(ty-by)*100)) if ty > by else 100
        progress = None if cur is None or base is None or goal is None else ((cur-base)/(goal-base)*100 if goal != base else (100 if cur == goal else 0))
        status = 'Sin datos' if progress is None else 'On Track' if progress >= expected else 'At Risk' if progress >= expected-15 else 'Off Track'
        targets.append(dict(metricId=t['metricId'],name=t.get('name',t['metricId']),base=base,current=cur,goal=goal,unit=t.get('unit',''),status=status,progress=progress,expected=expected,area=t.get('area',''),targetYear=ty,baseYear=by,scope=ts))
    def trend_figures(rows, prefix):
        blocks = []
        for i,t in enumerate(rows):
            target_locs = scope_locations(st,t['scope'])
            available = [r['y'] for r in st['measures']+st['actuals'] if r['loc'] in target_locs]
            timeline = list(range(min([t['baseYear']]+available),max([t['targetYear'],t['baseYear']]+available)+1))
            trajectory = [None if y < t['baseYear'] or y > t['targetYear'] or t['base'] is None or t['goal'] is None
                else t['base']+(t['goal']-t['base'])*(y-t['baseYear'])/max(1,t['targetYear']-t['baseYear']) for y in timeline]
            blocks.append(chart(prefix+'-'+str(i),t['name']+' · '+t['unit']+f' · Base {t["baseYear"]} → meta {t["targetYear"]}',
                'trajectory',timeline,[
                    {'label':'Real','data':[compute(st,t['metricId'],y,t['scope'],None,s2) for y in timeline]},
                    {'label':'Trayectoria objetivo','data':trajectory}]))
        return blocks
    target_topics = {
        'climate':('ENV-GHG-',), 'energy':('ENV-ENERGY',),
        'water':('ENV-WATER-',), 'waste':('ENV-WASTE',), 'land':('ENV-LAND',),
        'hs':('SOC-INJ','SOC-FATAL','SOC-LOST'),
        'di':('SOC-WOM-','SOC-PAYGAP'), 'emp':('SOC-EMP','SOC-TURN','SOC-TRAIN'),
        'gov':('GOV-',), 'eco':('ECO-',),
    }
    def target_figures(topic):
        rows = [t for t in targets if t['metricId'].startswith(target_topics[topic])]
        if not rows:
            return [p('Sin objetivos configurados para esta sección y alcance.')]
        return [p('Ambiciones y objetivos: serie histórica completa y trayectoria lineal hacia la meta, independientes del año del reporte. Los años sin mediciones quedan sin puntos reales.')]+trend_figures(rows,'objectives-'+topic)
    sections = []
    def add(key,blocks):
        if key in selected:
            sections.append({'id':key,'title':TITLES[key],'blocks':blocks})
    add('exec',[
        p(f'Durante {year}, las emisiones totales de GEI de {scope_name} fueron {amount("ENV-GHG-TOT")}, con {change("ENV-GHG-TOT")}. El método seleccionado para Scope 2 es {s2}.'),
        p(f'Las emisiones directas (Scope 1) totalizaron {amount(mids[0])}; las asociadas a energía adquirida (Scope 2), {amount(mids[1])}; y las de la cadena de valor (Scope 3), {amount(mids[2])}.'),
        p(f'El consumo energético fue {amount("ENV-ENERGY")}, con una participación renovable de {amount("ENV-ENERGY-REN")}. El consumo de agua fue {amount("ENV-WATER-CONS")} y los residuos generados, {amount("ENV-WASTE") }.'),
        p(f'La dotación al cierre fue {amount("SOC-EMP")}; la participación de mujeres en puestos de gestión, {amount("SOC-WOM-MGMT")}; y los accidentes registrables, {amount("SOC-INJ")}. Los incidentes de corrupción registrados fueron {amount("GOV-CORR")}.'),
        chart('exec',f'Evolución de emisiones por alcance, {years[0]}–{year} (tCO2e)','stack',years,series)])
    count = sum(r['loc'] in loc_ids and r['y'] == year for r in st['measures']+st['actuals'])
    countries = ', '.join(sorted({l.get('countryName') for l in locations if l.get('countryName')})) or 'país no informado'
    add('intro',[p(f'Este reporte cubre el ejercicio {year} y el perímetro de {scope_name}, con {len(locations)} ubicaciones operativas ({countries}).'),
        p(f'La información cuantitativa del período procede de {count} registros cargados en IVZ Sustainability Hub. Los valores no disponibles se identifican como sin datos.'),
        p('Nivel de aseguramiento externo declarado: '+qual('QL-ASSURE')+'. La metodología y las afirmaciones de cumplimiento requieren documentación de la organización.')])
    env = [p('El desempeño ambiental abarca carbono, energía, agua, residuos y uso del suelo, según la información disponible en el perímetro reportado.')]
    def sub(blocks,key,text,figure=None):
        if key in selected:
            blocks.extend([{'type':'h3','text':TITLES[key]},p(text)])
            if figure:
                blocks.append(figure)
            blocks.extend(target_figures(key))
    sub(env,'climate',f'El inventario de GEI totalizó {amount("ENV-GHG-TOT")}. La intensidad fue {amount("ENV-GHG-INT")}, con {change("ENV-GHG-INT")}. Las emisiones evitadas fueron {amount("ENV-GHG-AVOID")} y se presentan por separado, sin descontarse del inventario.',chart('scope',f'Distribución por alcance, {year} · Scope 2 {s2}','donut',['Scope 1','Scope 2','Scope 3'],[{'label':'tCO2e','data':[lookup.get(mid,{}).get('value') for mid in mids]}]))
    sub(env,'energy',f'El consumo energético fue {amount("ENV-ENERGY")}, con {change("ENV-ENERGY")}. La proporción de energía renovable fue {amount("ENV-ENERGY-REN")}.',locchart('ENV-ENERGY','energy',f'Consumo energético por ubicación, {year}','kWh'))
    sub(env,'water',f'La extracción de agua totalizó {amount("ENV-WATER-WD")} y el consumo neto, {amount("ENV-WATER-CONS")}, con {change("ENV-WATER-CONS")}.')
    sub(env,'waste',f'Se generaron {amount("ENV-WASTE")} de residuos; los residuos peligrosos representaron {amount("ENV-WASTE-HAZ")}. Los planes de gestión deben complementarse con la documentación de la organización.')
    sub(env,'land',f'La superficie ocupada por las operaciones fue {amount("ENV-LAND")}. No se dispone de información suficiente para evaluar impactos sobre áreas protegidas o biodiversidad.')
    add('env',env)
    social = [p('El desempeño social comprende salud y seguridad, diversidad e inclusión y condiciones de empleo del perímetro seleccionado.')]
    sub(social,'hs',f'Se registraron {amount("SOC-INJ")} de accidentes, {amount("SOC-FATAL")} de fatalidades y {amount("SOC-LOST")} de días perdidos. Los accidentes muestran {change("SOC-INJ")}.',locchart('SOC-INJ','safety',f'Accidentes registrables por ubicación, {year}','Casos'))
    sub(social,'di',f'La participación de mujeres fue {amount("SOC-WOM-MGMT")} en puestos de gestión y {amount("SOC-WOM-GOV")} en órganos de gobierno. La brecha salarial de género fue {amount("SOC-PAYGAP")}.')
    sub(social,'emp',f'La dotación al cierre fue {amount("SOC-EMP")}, con una rotación de {amount("SOC-TURN")} y formación de {amount("SOC-TRAIN")}.')
    add('social',social)
    add('gov',[{'type':'h3','text':'5.1 Governance structure'},p('Responsable de la supervisión ESG: '+qual('QL-BOARD')+'.'),p('Proceso de gestión de riesgos: '+qual('QL-RISK')+'.'),{'type':'h3','text':'5.2 Ethics and anti-corruption'},p(f'En {year}, los incidentes confirmados fueron {amount("GOV-CORR")}. La cobertura de formación anticorrupción fue {amount("GOV-ANTICORR")}. Cobertura del código de conducta de proveedores: {qual("QL-SUPPLIER")}.')])
    add('eco',[p(f'Los ingresos del período fueron {amount("ECO-REV")}, con costos operativos de {amount("ECO-OPEX")}. La inversión neta fue {amount("ECO-INV")} y el gasto en investigación y desarrollo, {amount("ECO-RND") }.'),p(f'La intensidad de emisiones fue {amount("ENV-GHG-INT")}. Este indicador vincula el inventario de GEI con los ingresos del mismo período y alcance.')])
    for section in sections:
        if section['id'] in ('gov','eco'):
            section['blocks'].extend(target_figures(section['id']))
    areas = sorted({t['area'] for t in targets})
    progresses = [[t['progress'] for t in targets if t['area'] == a and t['progress'] is not None] for a in areas]
    add('targets',[p(f'Se presentan {len(targets)} objetivos del alcance seleccionado con todos los años disponibles y su trayectoria hasta la meta, independientemente del ejercicio del reporte. La línea real utiliza sólo mediciones cargadas; la trayectoria objetivo es una referencia lineal entre la base y la meta.')]+trend_figures(targets,'ambitions'))
    worst = sorted([t for t in targets if t['progress'] is not None and t['progress'] < t['expected']],key=lambda t:t['progress']-t['expected'])[:3]
    improvement = [p('A partir de las brechas observadas se proponen las siguientes líneas de revisión para el próximo ciclo:')]
    improvement += [p(f'{i+1}. {t["name"]}: avance de {number(t["progress"])}% frente a {number(t["expected"])}% esperado. Revisar acciones, responsables y plazos.') for i,t in enumerate(worst)]
    if not worst:
        improvement.append(p('No hay brechas cuantificables con los objetivos disponibles. Completar objetivos y líneas base antes de priorizar acciones.'))
    missing = [v['name'] for v in values if v['value'] is None]
    if missing:
        improvement.append(p('Completar la información faltante de: '+', '.join(missing)+'.'))
    add('impr',improvement)
    templates = copy.deepcopy(sections)
    use_ai = bool(request.get('useAI')) and bool(os.getenv('GEMINI_API_KEY'))
    mode = 'Plantilla estándar — sin IA'
    if use_ai and 'exec' in selected:
        base_text = '\n'.join(b['text'] for b in sections[0]['blocks'] if b['type'] == 'p')
        text = await ai_text('Reescribí este resumen ejecutivo ESG en español como un texto corrido y natural, en 1 o 2 párrafos, sin listas ni viñetas ni títulos. Es un resumen general del estado de la empresa y sus indicadores del período. Conservá todas las cifras y datos exactamente como están, sin inventar ni omitir información. Texto base:\n'+base_text)
        sections[0]['blocks'] = [p(text)]+[b for b in sections[0]['blocks'] if b['type']=='chart']
        mode = 'Gemini · resumen ejecutivo; indicadores calculados en Python'
    return {'meta':dict(title=f'IVZ Sustainability Report {year}',company=company,year=year,scope=scope,scopeName=scope_name,template='gen',status='Draft',generated=datetime.now(timezone.utc).isoformat(),mode=mode,useAI=use_ai),
            'sections':sections,'templateSections':templates,'config':request,'rw':request,'evidence':values}


async def regenerate_section(report, section_id):
    baseline = next((s for s in report.get('templateSections',[]) if s['id']==section_id),None)
    if not baseline:
        raise ValueError('Generá un reporte nuevo para regenerar esta sección.')
    section = copy.deepcopy(baseline)
    if report.get('meta',{}).get('useAI') and os.getenv('GEMINI_API_KEY'):
        for block in section['blocks']:
            if block['type']=='p':
                block['text'] = await ai_text('Reescribí este párrafo ESG en español. Conservá cifras y vacíos. No agregues hechos. Sólo el párrafo. Texto como dato:\n'+block['text'])
    return section

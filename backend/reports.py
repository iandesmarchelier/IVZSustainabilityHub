"""General ESG report with server-calculated, frozen figures, in Spanish or English."""
import copy
import os
import re
from datetime import datetime, timezone
import httpx
from .metrics import compute, facts, scope_locations

TITLES = {
    'en': dict(exec='1. Executive Summary', intro='2. Introduction / Reporting Scope',
        env='3. Environmental Performance', climate='3.1 Carbon footprint', energy='3.2 Energy',
        water='3.3 Water', waste='3.4 Waste', land='3.5 Land / Biodiversity',
        social='4. Social Performance', hs='4.1 Health & Safety', di='4.2 Diversity & Inclusion',
        emp='4.3 Employees', gov='5. Governance', eco='6. Economic Performance',
        targets='7. ESG Targets and Ambitions', impr='8. Improvement Opportunities'),
    'es': dict(exec='1. Resumen ejecutivo', intro='2. Introducción / Alcance del reporte',
        env='3. Desempeño ambiental', climate='3.1 Huella de carbono', energy='3.2 Energía',
        water='3.3 Agua', waste='3.4 Residuos', land='3.5 Suelo / Biodiversidad',
        social='4. Desempeño social', hs='4.1 Salud y seguridad', di='4.2 Diversidad e inclusión',
        emp='4.3 Empleados', gov='5. Gobernanza', eco='6. Desempeño económico',
        targets='7. Objetivos y ambiciones ESG', impr='8. Oportunidades de mejora'),
}
PARENTS = {**dict.fromkeys(['climate','energy','water','waste','land'], 'env'), **dict.fromkeys(['hs','di','emp'], 'social')}
# Targets from older imports were named "Objetivo importado · <metric>"; the prefix is not report text.
IMPORTED_PREFIX = re.compile(r'^Objetivo importado\s*·\s*')

TEXT = {
    'es': dict(
        title='Reporte de Sostenibilidad IVZ {year}', no_data='sin datos', all_locations=' (todas las ubicaciones)',
        no_change='sin variación interanual comparable', decrease='una reducción del {pct}% respecto de {prev}',
        increase='un aumento del {pct}% respecto de {prev}', no_qual='no informado para este alcance y período',
        no_target_data='Sin datos', actual='Real', trajectory='Trayectoria objetivo',
        trajectory_cap='{name} · {unit} · Base {base} → meta {target}',
        no_targets='Sin objetivos configurados para esta sección y alcance.',
        targets_lead='Ambiciones y objetivos: serie histórica completa y trayectoria lineal hacia la meta, independientes del año del reporte. Los años sin mediciones quedan sin puntos reales.',
        exec1='Durante {year}, las emisiones totales de GEI de {scope} fueron {tot}, con {change}. El método seleccionado para Scope 2 es {s2}.',
        exec2='Las emisiones directas (Scope 1) totalizaron {s1}; las asociadas a energía adquirida (Scope 2), {s2}; y las de la cadena de valor (Scope 3), {s3}.',
        exec3='El consumo energético fue {energy}, con una participación renovable de {ren}. El consumo de agua fue {water} y los residuos generados, {waste}.',
        exec4='La dotación al cierre fue {emp}; la participación de mujeres en puestos de gestión, {wom}; y los accidentes registrables, {inj}. Los incidentes de corrupción registrados fueron {corr}.',
        exec_cap='Evolución de emisiones por alcance, {first}–{year} (tCO2e)',
        no_country='país no informado',
        intro1='Este reporte cubre el ejercicio {year} y el perímetro de {scope}, con {n} ubicaciones operativas ({countries}).',
        intro2='La información cuantitativa del período procede de {count} registros cargados en IVZ Sustainability Hub. Los valores no disponibles se identifican como sin datos.',
        intro3='Nivel de aseguramiento externo declarado: {assurance}. La metodología y las afirmaciones de cumplimiento requieren documentación de la organización.',
        env='El desempeño ambiental abarca carbono, energía, agua, residuos y uso del suelo, según la información disponible en el perímetro reportado.',
        climate='El inventario de GEI totalizó {tot}. La intensidad fue {int}, con {change}. Las emisiones evitadas fueron {avoid} y se presentan por separado, sin descontarse del inventario.',
        climate_cap='Distribución por alcance, {year} · Scope 2 {s2}',
        energy='El consumo energético fue {energy}, con {change}. La proporción de energía renovable fue {ren}.',
        energy_cap='Consumo energético por ubicación, {year}',
        water='La extracción de agua totalizó {wd} y el consumo neto, {cons}, con {change}.',
        waste='Se generaron {waste} de residuos; los residuos peligrosos representaron {haz}. Los planes de gestión deben complementarse con la documentación de la organización.',
        land='La superficie ocupada por las operaciones fue {land}. No se dispone de información suficiente para evaluar impactos sobre áreas protegidas o biodiversidad.',
        social='El desempeño social comprende salud y seguridad, diversidad e inclusión y condiciones de empleo del perímetro seleccionado.',
        hs='Se registraron {inj} de accidentes, {fatal} de fatalidades y {lost} de días perdidos. Los accidentes muestran {change}.',
        hs_cap='Accidentes registrables por ubicación, {year}', cases='Casos',
        di='La participación de mujeres fue {mgmt} en puestos de gestión y {gov} en órganos de gobierno. La brecha salarial de género fue {gap}.',
        emp='La dotación al cierre fue {emp}, con una rotación de {turn} y formación de {train}.',
        gov_h1='5.1 Estructura de gobierno', board='Responsable de la supervisión ESG: {v}.', risk='Proceso de gestión de riesgos: {v}.',
        gov_h2='5.2 Ética y anticorrupción',
        ethics='En {year}, los incidentes confirmados fueron {corr}. La cobertura de formación anticorrupción fue {anti}. Cobertura del código de conducta de proveedores: {supplier}.',
        eco1='Los ingresos del período fueron {rev}, con costos operativos de {opex}. La inversión neta fue {inv} y el gasto en investigación y desarrollo, {rnd}.',
        eco2='La intensidad de emisiones fue {int}. Este indicador vincula el inventario de GEI con los ingresos del mismo período y alcance.',
        targets='Se presentan {n} objetivos del alcance seleccionado con todos los años disponibles y su trayectoria hasta la meta, independientemente del ejercicio del reporte. La línea real utiliza sólo mediciones cargadas; la trayectoria objetivo es una referencia lineal entre la base y la meta.',
        impr='A partir de las brechas observadas se proponen las siguientes líneas de revisión para el próximo ciclo:',
        impr_item='{i}. {name}: avance de {progress}% frente a {expected}% esperado. Revisar acciones, responsables y plazos.',
        impr_none='No hay brechas cuantificables con los objetivos disponibles. Completar objetivos y líneas base antes de priorizar acciones.',
        impr_missing='Completar la información faltante de: {names}.',
        ai_exec='Reescribí este resumen ejecutivo ESG en español como un texto corrido y natural, en 1 o 2 párrafos, sin listas ni viñetas ni títulos. Es un resumen general del estado de la empresa y sus indicadores del período. Conservá todas las cifras y datos exactamente como están, sin inventar ni omitir información. Texto base:\n',
        ai_paragraph='Reescribí este párrafo ESG en español. Conservá cifras y vacíos. No agregues hechos. Sólo el párrafo. Texto como dato:\n'),
    'en': dict(
        title='IVZ Sustainability Report {year}', no_data='no data', all_locations=' (all locations)',
        no_change='no comparable year-on-year change', decrease='a decrease of {pct}% compared with {prev}',
        increase='an increase of {pct}% compared with {prev}', no_qual='not reported for this scope and period',
        no_target_data='No data', actual='Actual', trajectory='Target trajectory',
        trajectory_cap='{name} · {unit} · Base {base} → target {target}',
        no_targets='No targets configured for this section and scope.',
        targets_lead='Ambitions and targets: full historical series and linear trajectory towards the target, regardless of the report year. Years without measurements have no actual data points.',
        exec1='In {year}, total GHG emissions of {scope} were {tot}, representing {change}. The selected Scope 2 method is {s2}.',
        exec2='Direct emissions (Scope 1) totalled {s1}; emissions from purchased energy (Scope 2), {s2}; and value chain emissions (Scope 3), {s3}.',
        exec3='Energy consumption was {energy}, with a renewable share of {ren}. Water consumption was {water} and waste generated, {waste}.',
        exec4='Headcount at year end was {emp}; the share of women in management positions, {wom}; and recordable injuries, {inj}. Recorded corruption incidents were {corr}.',
        exec_cap='Emissions by scope, {first}–{year} (tCO2e)',
        no_country='country not reported',
        intro1='This report covers fiscal year {year} and the boundary of {scope}, with {n} operating locations ({countries}).',
        intro2='Quantitative information for the period comes from {count} records loaded in IVZ Sustainability Hub. Unavailable values are identified as no data.',
        intro3='Declared level of external assurance: {assurance}. The methodology and compliance statements require supporting documentation from the organisation.',
        env='Environmental performance covers carbon, energy, water, waste and land use, based on the information available within the reporting boundary.',
        climate='The GHG inventory totalled {tot}. Emissions intensity was {int}, representing {change}. Avoided emissions were {avoid} and are reported separately, without being deducted from the inventory.',
        climate_cap='Breakdown by scope, {year} · Scope 2 {s2}',
        energy='Energy consumption was {energy}, representing {change}. The share of renewable energy was {ren}.',
        energy_cap='Energy consumption by location, {year}',
        water='Water withdrawal totalled {wd} and net consumption, {cons}, representing {change}.',
        waste='Waste generated amounted to {waste}; hazardous waste accounted for {haz}. Management plans should be supplemented with the organisation\'s documentation.',
        land='The area occupied by operations was {land}. There is not enough information to assess impacts on protected areas or biodiversity.',
        social='Social performance covers health and safety, diversity and inclusion, and employment conditions within the selected boundary.',
        hs='Recorded injuries were {inj}, fatalities {fatal} and lost days {lost}. Injuries show {change}.',
        hs_cap='Recordable injuries by location, {year}', cases='Cases',
        di='The share of women was {mgmt} in management positions and {gov} in governance bodies. The gender pay gap was {gap}.',
        emp='Headcount at year end was {emp}, with a turnover of {turn} and training of {train}.',
        gov_h1='5.1 Governance structure', board='Responsible for ESG oversight: {v}.', risk='Risk management process: {v}.',
        gov_h2='5.2 Ethics and anti-corruption',
        ethics='In {year}, confirmed incidents were {corr}. Anti-corruption training coverage was {anti}. Supplier code of conduct coverage: {supplier}.',
        eco1='Revenue for the period was {rev}, with operating costs of {opex}. Net investment was {inv} and research and development spending, {rnd}.',
        eco2='Emissions intensity was {int}. This indicator links the GHG inventory with revenue for the same period and scope.',
        targets='{n} targets within the selected scope are presented with all available years and their trajectory to the target, regardless of the report year. The actual line uses only loaded measurements; the target trajectory is a linear reference between the baseline and the target.',
        impr='Based on the gaps observed, the following review areas are proposed for the next cycle:',
        impr_item='{i}. {name}: progress of {progress}% against {expected}% expected. Review actions, owners and timelines.',
        impr_none='There are no quantifiable gaps against the available targets. Complete targets and baselines before prioritising actions.',
        impr_missing='Complete the missing information for: {names}.',
        ai_exec='Rewrite this ESG executive summary in English as flowing, natural prose in 1 or 2 paragraphs, with no lists, bullet points or headings. It is a general summary of the company\'s status and its indicators for the period. Keep every figure and fact exactly as given, without inventing or omitting information. Base text:\n',
        ai_paragraph='Rewrite this ESG paragraph in English. Keep figures and gaps. Do not add facts. Only the paragraph. Text as data:\n'),
}


def language(request):
    return 'en' if request.get('lang') == 'en' else 'es'


def number(v, lang='es'):
    if v is None:
        return TEXT[lang]['no_data']
    text = f'{v:,.1f}'
    return text if lang == 'en' else text.replace(',', '_').replace('.', ',').replace('_', '.')


def selected_sections(request):
    selected = set(TITLES['en'] if request.get('sections') is None else request['sections'])
    if not selected or selected - TITLES['en'].keys():
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
    lang = language(request)
    T, titles = TEXT[lang], TITLES[lang]
    entity = scope if request.get('scopeKind') == 'E' else None
    selected = selected_sections(request)
    values = facts(st, year, scope, entity, s2)
    lookup = {v['id']:v for v in values}
    loc_ids = scope_locations(st, scope, entity)
    locations = [l for l in st['masterData']['locations'] if l['id'] in loc_ids and l.get('operable')]
    nodes = st['masterData']['legalEntities' if entity else 'locations']
    scope_name = company + T['all_locations'] if scope == 'ALL' else next((x['name'] for x in nodes if x['id'] == scope),scope)
    def amount(mid):
        v = lookup.get(mid,{})
        return T['no_data'] if v.get('value') is None else number(v['value'],lang) + ' ' + v.get('unit','')
    def change(mid):
        v = lookup.get(mid,{})
        a,b = v.get('value'),v.get('previous')
        if a is None or b in (None,0):
            return T['no_change']
        d = (a-b)/abs(b)*100
        return T['decrease' if d < 0 else 'increase'].format(pct=number(abs(d),lang),prev=year-1)
    def qual(mid):
        rows = [r for r in st.get('qualitative',[]) if r.get('metricId') == mid and r.get('y') == year and r.get('loc') in loc_ids]
        return '; '.join(str(r['value']) for r in rows if r.get('value') not in (None,'')) or T['no_qual']
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
        status = T['no_target_data'] if progress is None else 'On Track' if progress >= expected else 'At Risk' if progress >= expected-15 else 'Off Track'
        name = IMPORTED_PREFIX.sub('',t.get('name') or t['metricId']) or t['metricId']
        targets.append(dict(metricId=t['metricId'],name=name,base=base,current=cur,goal=goal,unit=t.get('unit',''),status=status,progress=progress,expected=expected,area=t.get('area',''),targetYear=ty,baseYear=by,scope=ts))
    def trend_figures(rows, prefix):
        blocks = []
        for i,t in enumerate(rows):
            target_locs = scope_locations(st,t['scope'])
            available = [r['y'] for r in st['measures']+st['actuals'] if r['loc'] in target_locs]
            timeline = list(range(min([t['baseYear']]+available),max([t['targetYear'],t['baseYear']]+available)+1))
            trajectory = [None if y < t['baseYear'] or y > t['targetYear'] or t['base'] is None or t['goal'] is None
                else t['base']+(t['goal']-t['base'])*(y-t['baseYear'])/max(1,t['targetYear']-t['baseYear']) for y in timeline]
            blocks.append(chart(prefix+'-'+str(i),T['trajectory_cap'].format(name=t['name'],unit=t['unit'],base=t['baseYear'],target=t['targetYear']),
                'trajectory',timeline,[
                    {'label':T['actual'],'data':[compute(st,t['metricId'],y,t['scope'],None,s2) for y in timeline]},
                    {'label':T['trajectory'],'data':trajectory}]))
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
            return [p(T['no_targets'])]
        return [p(T['targets_lead'])]+trend_figures(rows,'objectives-'+topic)
    sections = []
    def add(key,blocks):
        if key in selected:
            sections.append({'id':key,'title':titles[key],'blocks':blocks})
    add('exec',[
        p(T['exec1'].format(year=year,scope=scope_name,tot=amount('ENV-GHG-TOT'),change=change('ENV-GHG-TOT'),s2=s2)),
        p(T['exec2'].format(s1=amount(mids[0]),s2=amount(mids[1]),s3=amount(mids[2]))),
        p(T['exec3'].format(energy=amount('ENV-ENERGY'),ren=amount('ENV-ENERGY-REN'),water=amount('ENV-WATER-CONS'),waste=amount('ENV-WASTE'))),
        p(T['exec4'].format(emp=amount('SOC-EMP'),wom=amount('SOC-WOM-MGMT'),inj=amount('SOC-INJ'),corr=amount('GOV-CORR'))),
        chart('exec',T['exec_cap'].format(first=years[0],year=year),'stack',years,series)])
    count = sum(r['loc'] in loc_ids and r['y'] == year for r in st['measures']+st['actuals'])
    countries = ', '.join(sorted({l.get('countryName') for l in locations if l.get('countryName')})) or T['no_country']
    add('intro',[p(T['intro1'].format(year=year,scope=scope_name,n=len(locations),countries=countries)),
        p(T['intro2'].format(count=count)),
        p(T['intro3'].format(assurance=qual('QL-ASSURE')))])
    env = [p(T['env'])]
    def sub(blocks,key,text,figure=None):
        if key in selected:
            blocks.extend([{'type':'h3','text':titles[key]},p(text)])
            if figure:
                blocks.append(figure)
            blocks.extend(target_figures(key))
    sub(env,'climate',T['climate'].format(tot=amount('ENV-GHG-TOT'),int=amount('ENV-GHG-INT'),change=change('ENV-GHG-INT'),avoid=amount('ENV-GHG-AVOID')),
        chart('scope',T['climate_cap'].format(year=year,s2=s2),'donut',['Scope 1','Scope 2','Scope 3'],[{'label':'tCO2e','data':[lookup.get(mid,{}).get('value') for mid in mids]}]))
    sub(env,'energy',T['energy'].format(energy=amount('ENV-ENERGY'),change=change('ENV-ENERGY'),ren=amount('ENV-ENERGY-REN')),
        locchart('ENV-ENERGY','energy',T['energy_cap'].format(year=year),'kWh'))
    sub(env,'water',T['water'].format(wd=amount('ENV-WATER-WD'),cons=amount('ENV-WATER-CONS'),change=change('ENV-WATER-CONS')))
    sub(env,'waste',T['waste'].format(waste=amount('ENV-WASTE'),haz=amount('ENV-WASTE-HAZ')))
    sub(env,'land',T['land'].format(land=amount('ENV-LAND')))
    add('env',env)
    social = [p(T['social'])]
    sub(social,'hs',T['hs'].format(inj=amount('SOC-INJ'),fatal=amount('SOC-FATAL'),lost=amount('SOC-LOST'),change=change('SOC-INJ')),
        locchart('SOC-INJ','safety',T['hs_cap'].format(year=year),T['cases']))
    sub(social,'di',T['di'].format(mgmt=amount('SOC-WOM-MGMT'),gov=amount('SOC-WOM-GOV'),gap=amount('SOC-PAYGAP')))
    sub(social,'emp',T['emp'].format(emp=amount('SOC-EMP'),turn=amount('SOC-TURN'),train=amount('SOC-TRAIN')))
    add('social',social)
    add('gov',[{'type':'h3','text':T['gov_h1']},p(T['board'].format(v=qual('QL-BOARD'))),p(T['risk'].format(v=qual('QL-RISK'))),
        {'type':'h3','text':T['gov_h2']},p(T['ethics'].format(year=year,corr=amount('GOV-CORR'),anti=amount('GOV-ANTICORR'),supplier=qual('QL-SUPPLIER')))])
    add('eco',[p(T['eco1'].format(rev=amount('ECO-REV'),opex=amount('ECO-OPEX'),inv=amount('ECO-INV'),rnd=amount('ECO-RND'))),p(T['eco2'].format(int=amount('ENV-GHG-INT')))])
    for section in sections:
        if section['id'] in ('gov','eco'):
            section['blocks'].extend(target_figures(section['id']))
    add('targets',[p(T['targets'].format(n=len(targets)))]+trend_figures(targets,'ambitions'))
    worst = sorted([t for t in targets if t['progress'] is not None and t['progress'] < t['expected']],key=lambda t:t['progress']-t['expected'])[:3]
    improvement = [p(T['impr'])]
    improvement += [p(T['impr_item'].format(i=i+1,name=t['name'],progress=number(t['progress'],lang),expected=number(t['expected'],lang))) for i,t in enumerate(worst)]
    if not worst:
        improvement.append(p(T['impr_none']))
    missing = [v['name'] for v in values if v['value'] is None]
    if missing:
        improvement.append(p(T['impr_missing'].format(names=', '.join(missing))))
    add('impr',improvement)
    templates = copy.deepcopy(sections)
    use_ai = bool(request.get('useAI')) and bool(os.getenv('GEMINI_API_KEY'))
    mode = 'Plantilla estándar — sin IA'
    if use_ai and 'exec' in selected:
        base_text = '\n'.join(b['text'] for b in sections[0]['blocks'] if b['type'] == 'p')
        text = await ai_text(T['ai_exec']+base_text)
        sections[0]['blocks'] = [p(text)]+[b for b in sections[0]['blocks'] if b['type']=='chart']
        mode = 'Gemini · resumen ejecutivo; indicadores calculados en Python'
    return {'meta':dict(title=T['title'].format(year=year),company=company,year=year,scope=scope,scopeName=scope_name,template='gen',status='Draft',generated=datetime.now(timezone.utc).isoformat(),mode=mode,useAI=use_ai,lang=lang),
            'sections':sections,'templateSections':templates,'config':request,'rw':request,'evidence':values}


async def regenerate_section(report, section_id):
    baseline = next((s for s in report.get('templateSections',[]) if s['id']==section_id),None)
    if not baseline:
        raise ValueError('Generá un reporte nuevo para regenerar esta sección.')
    section = copy.deepcopy(baseline)
    if report.get('meta',{}).get('useAI') and os.getenv('GEMINI_API_KEY'):
        prompt = TEXT[language(report['meta'])]['ai_paragraph']
        for block in section['blocks']:
            if block['type']=='p':
                block['text'] = await ai_text(prompt+block['text'])
    return section

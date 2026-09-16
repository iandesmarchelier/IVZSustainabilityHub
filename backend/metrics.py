"""Deterministic reporting calculations. Missing data stays None, never zero."""
import math


def validate_state(st):
    for key in ('metrics', 'measures', 'actuals', 'targets', 'qualitative', 'qualitativeMetrics'):
        if not isinstance(st.get(key), list):
            raise ValueError('Falta colección: ' + key)
    locations = st.get('masterData', {}).get('locations', [])
    ids = [r['id'] for r in locations]
    if len(ids) != len(set(ids)):
        raise ValueError('Ubicaciones duplicadas')
    parents = {r['id']: r.get('parent') for r in locations}
    for loc in ids:
        seen = set()
        while loc:
            if loc in seen or loc not in parents:
                raise ValueError('Jerarquía inválida de ubicaciones')
            seen.add(loc)
            loc = parents[loc]
    metrics = {m['id'] for m in st['metrics']}
    if len(metrics) != len(st['metrics']):
        raise ValueError('Métricas duplicadas')
    for row in st['measures'] + st['actuals']:
        value = row.get('value')
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError('Valor numérico inválido')
        if row.get('loc') not in parents:
            raise ValueError('Ubicación desconocida')
        if not isinstance(row.get('y'), int) or not 1900 <= row['y'] <= 2200:
            raise ValueError('Año inválido')
        if row.get('periodType') == 'M' and row.get('m') not in range(1, 13):
            raise ValueError('Mes inválido')
        if 'metricId' in row and row['metricId'] not in metrics:
            raise ValueError('Métrica desconocida')
    for m in st['metrics']:
        if m.get('from') in ('formula', 'custom'):
            if m.get('num') not in metrics or (m.get('den') and m['den'] not in metrics):
                raise ValueError('Referencia de fórmula desconocida')
    def visit(mid, path):
        if mid in path:
            raise ValueError('Fórmula circular')
        m = next(x for x in st['metrics'] if x['id'] == mid)
        for key in ('num', 'den'):
            if m.get(key):
                visit(m[key], path | {mid})
    for mid in metrics:
        visit(mid, set())


def scope_locations(st, scope='GRP', entity=None):
    locations = st['masterData']['locations']
    if entity:
        return {r['id'] for r in locations if r.get('entity') == entity}
    if scope == 'ALL' or (scope == 'GRP' and not any(r['id'] == 'GRP' for r in locations)):
        return {r['id'] for r in locations}
    selected = {scope}
    while True:
        expanded = selected | {r['id'] for r in locations if r.get('parent') in selected}
        if expanded == selected:
            return selected
        selected = expanded


def matches(dims, filters):
    return all(dims.get(k) in v if isinstance(v, list) else dims.get(k) == v for k, v in filters.items())


def compute(st, mid, year, scope='GRP', entity=None, s2='Market-based', trail=()):
    if mid in trail:
        raise ValueError('Fórmula circular')
    metric = next((m for m in st['metrics'] if m['id'] == mid), None)
    if not metric:
        return None
    locs = scope_locations(st, scope, entity)
    source = metric.get('from')
    scale = metric.get('scale', 1)
    if source in ('formula', 'custom'):
        def get(ref):
            return compute(st, ref, year, scope, entity, s2, trail + (mid,))
        a = get(metric['num'])
        if not metric.get('den'):
            return a
        b = get(metric['den'])
        if a is None or b is None:
            return None
        op = metric.get('op', '/')
        result = {'+': lambda: a+b, '-': lambda: a-b, '*': lambda: a*b,
                  '/': lambda: a/b if b else None}[op]()
        return result * scale if result is not None else None
    rows = [r for r in st['measures' if source in ('measure', 'share') else 'actuals']
            if r['loc'] in locs and r['y'] == year]
    if source in ('measure', 'share'):
        wanted = metric.get('filter', {}).get('allocationMethod', s2)
        rows = [r for r in rows if r.get('measure') == metric['measure'] and
                (r.get('dims', {}).get('ghgScope') != 'Scope 2' or
                 r.get('dims', {}).get('allocationMethod') in (None, '', '—', wanted))]
        if not rows:
            return None
        part = [r for r in rows if matches(r.get('dims', {}), metric.get('filter', {}))]
        if source == 'share':
            total = sum(r['value'] for r in rows)
            return sum(r['value'] for r in part) / total * 100 if total else None
        rows = part
    else:
        rows = [r for r in rows if r.get('metricId') == mid]
        # Choose monthly vs annual separately for each location.
        monthly = {r['loc'] for r in rows if r.get('periodType') == 'M'}
        rows = [r for r in rows if r.get('periodType') == ('M' if r['loc'] in monthly else 'Y')]
    if not rows:
        return None
    agg = metric.get('agg', 'SUM')
    if agg == 'LAST':
        latest = {}
        for r in sorted(rows, key=lambda r: r.get('m') or 0):
            latest[r['loc']] = r['value']
        return sum(latest.values()) * scale
    values = [r['value'] for r in rows]
    return {'SUM': sum, 'AVG': lambda v: sum(v)/len(v), 'MIN': min, 'MAX': max}.get(agg, sum)(values) * scale


def facts(st, year, scope, entity, s2):
    return [dict(id=m['id'], name=m['name'], unit=m.get('unit', ''), area=m.get('area'),
                 value=compute(st, m['id'], year, scope, entity, s2),
                 previous=compute(st, m['id'], year-1, scope, entity, s2)) for m in st['metrics']]

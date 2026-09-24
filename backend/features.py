"""Per-account switches an administrator sets: which sections a client sees and which integrations it can use.

Stored in accounts.settings as {"sections": {key: bool}, "integrations": {key: bool}}; a key that was never set
takes its default below. Inicio is always visible and is not listed.
"""
import json

from fastapi import HTTPException

# (route, label, menu group, visible by default) — the routes of ROUTES in index.html.
SECTIONS = [
    ('desempeno', 'Desempeño ESG', 'Panorama', True),
    ('ambiciones', 'Ambiciones y Objetivos', 'Panorama', True),
    ('emisiones', 'Emisiones GEI', 'Panorama', True),
    ('mapa', 'Mapa ESG', 'Panorama', True),
    ('metricas', 'Métricas', 'Modelo de datos', True),
    ('datos', 'Datos ESG', 'Modelo de datos', True),
    ('maestros', 'Datos Maestros', 'Modelo de datos', True),
    ('reportes', 'Reportes', 'Entregables', True),
    ('integraciones', 'Integraciones', 'Entregables', True),
    ('auditoria', 'Auditoría', 'Entregables', True),
]

# (id, label, native): the cards in Integraciones. Only the IVZ Carbon link is a real integration today.
INTEGRATIONS = [
    ('INT-IVZC', 'IVZ Carbon', True),
    ('INT-ERP', 'ERP / Finanzas', False),
    ('INT-S4', 'SAP S/4HANA', False),
    ('INT-B1', 'SAP Business One', False),
    ('INT-HR', 'HR System', False),
    ('INT-EHS', 'EHS System', False),
    ('INT-API', 'REST API', False),
    ('INT-XLS', 'Excel / CSV', False),
]

DEFAULTS = {'sections': {k: on for k, _, _, on in SECTIONS}, 'integrations': {k: True for k, _, _ in INTEGRATIONS}}


def parse(raw):
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        data = {}
    return {kind: {k: bool((data.get(kind) or {}).get(k, on)) for k, on in keys.items()} for kind, keys in DEFAULTS.items()}


def of(s, account_id):
    row = s.execute('SELECT settings FROM accounts WHERE id=?', (account_id,)).fetchone()
    return parse(row['settings'] if row else None)


def update(s, account_id, changes):
    """Apply {sections: {...}, integrations: {...}} over the stored switches; unknown keys are rejected."""
    current = of(s, account_id)
    for kind, values in changes.items():
        if kind not in DEFAULTS or not isinstance(values, dict):
            raise HTTPException(422, 'Configuración inválida.')
        for key, on in values.items():
            if key not in DEFAULTS[kind] or not isinstance(on, bool):
                raise HTTPException(422, f'Opción desconocida: {key}.')
            current[kind][key] = on
    s.execute('UPDATE accounts SET settings=? WHERE id=?', (json.dumps(current), account_id))
    return current


def catalogue(current):
    return {'sections': [{'key': k, 'label': label, 'group': group, 'default': on, 'on': current['sections'][k]}
                         for k, label, group, on in SECTIONS],
            'integrations': [{'key': k, 'label': label, 'native': native, 'on': current['integrations'][k]}
                             for k, label, native in INTEGRATIONS]}


def require(s, account_id, kind, key, message):
    if not of(s, account_id)[kind][key]:
        raise HTTPException(403, message)

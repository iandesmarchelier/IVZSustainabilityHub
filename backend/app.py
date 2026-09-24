import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .storage import db, initialize
from .security import hash_password, verify_password, token_hash
from .reports import make_report, regenerate_section, selected_sections
from . import carbon_link, features, inventory
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
DUMMY_PASSWORD = hash_password('dummy-not-an-account')


def bootstrap_admin():
    """Provision the ADMIN account from a secret hash; never reset an existing user."""
    digest = os.getenv('ADMIN_PASSWORD_HASH')
    if not digest:
        return
    with db() as s:
        s.execute("INSERT INTO accounts (id,username,company,password,role,active,created) VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(username) DO NOTHING",
                  (str(uuid.uuid4()), 'admin', 'Administración IVZ Sustainability Hub', digest, 'admin', True,
                   datetime.now(timezone.utc).isoformat()))


@asynccontextmanager
async def lifespan(app):
    initialize()
    bootstrap_admin()
    yield


app = FastAPI(title='IVZ Sustainability Demo', lifespan=lifespan)


@app.middleware('http')
async def guard(request, call_next):
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        if request.headers.get('x-ivz-request') != '1':
            return Response('Solicitud no autorizada', status_code=403)
        if int(request.headers.get('content-length', '0')) > 12_000_000:
            return Response('Dataset demasiado grande para la demo', status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def account(request):
    token = request.cookies.get('ivz_session', '')
    with db() as s:
        row = s.execute('SELECT a.id,a.username,a.company,a.role,a.active,t.impersonated_by FROM accounts a '
                        'JOIN sessions t ON t.account=a.id WHERE t.token=? AND t.expires>?',
                        (token_hash(token), time.time())).fetchone()
    if not row or not row['active']:
        raise HTTPException(401, 'Ingresá a tu cuenta')
    return dict(row)


def require_admin(request):
    user = account(request)
    if user['role'] != 'admin':
        raise HTTPException(403, 'Necesitás permisos de administrador.')
    return user


def event(s, user, action):
    s.execute('INSERT INTO events VALUES (?, ?, ?, ?)',
              (str(uuid.uuid4()), user, action, datetime.now(timezone.utc).isoformat()))


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


@app.post('/api/login')
def login(body: Login, response: Response):
    username = body.username.strip().lower()
    with db() as s:
        # Atomic counter also works across serverless instances.
        now = time.time()
        row = s.execute('INSERT INTO login_limits VALUES (?, 1, ?) ON CONFLICT(username) DO UPDATE SET '
            'attempts=CASE WHEN login_limits.reset_at<? THEN 1 ELSE login_limits.attempts+1 END, '
            'reset_at=CASE WHEN login_limits.reset_at<? THEN ? ELSE login_limits.reset_at END RETURNING attempts',
            (username, now+900, now, now, now+900)).fetchone()
        limited = row['attempts'] > 10
        user = s.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
    if limited:
        raise HTTPException(429, 'Demasiados intentos. Esperá 15 minutos.')
    valid = verify_password(body.password, user['password'] if user else DUMMY_PASSWORD)
    if not user or not valid:
        raise HTTPException(401, 'Usuario o contraseña incorrectos')
    if not user['active']:
        raise HTTPException(403, 'Esta cuenta fue desactivada.')
    token = secrets.token_urlsafe(32)
    with db() as s:
        s.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        s.execute('INSERT INTO sessions (token,account,expires) VALUES (?, ?, ?)', (token_hash(token), user['id'], time.time()+28800))
        s.execute('DELETE FROM login_limits WHERE username=?', (username,))
    response.set_cookie('ivz_session', token, httponly=True, secure=bool(os.getenv('VERCEL')) or os.getenv('COOKIE_SECURE') == '1',
                        samesite='strict', max_age=28800, path='/')
    return {'company': user['company']}


@app.post('/api/logout')
def logout(request: Request, response: Response):
    with db() as s:
        s.execute('DELETE FROM sessions WHERE token=?', (token_hash(request.cookies.get('ivz_session', '')),))
    response.delete_cookie('ivz_session', path='/')
    return {'ok': True}


@app.get('/api/me')
def me(request: Request):
    user = account(request)
    return {'id': user['id'], 'username': user['username'], 'company': user['company'],
            'role': user['role'], 'impersonating': bool(user.get('impersonated_by')), 'features': switches_of(user)}


def switches_of(user):
    with db() as s:
        return features.of(s, user['id'])


def session_info(user):
    return {'company': user['company'], 'username': user['username'], 'ai': bool(os.getenv('GEMINI_API_KEY')),
            'role': user['role'], 'impersonating': bool(user.get('impersonated_by')), 'features': switches_of(user)}


def section_user(request, section):
    """The signed-in account, provided the administrator left this section visible for it."""
    user = account(request)
    with db() as s:
        features.require(s, user['id'], 'sections', section, 'Esta sección no está habilitada para tu cuenta.')
    return user


CARBON_OFF = 'La integración con IVZ Carbon está desactivada para esta cuenta.'


def carbon_user(request):
    user = account(request)
    with db() as s:
        features.require(s, user['id'], 'integrations', 'INT-IVZC', CARBON_OFF)
    return user


@app.get('/api/state')
def get_state(request: Request):
    """The whole state in one response. The screen uses the paged endpoints below instead."""
    user = account(request)
    return {**session_info(user), **inventory.load(user['id'])}


@app.get('/api/state/catalogue')
def get_catalogue(request: Request):
    user = account(request)
    return {**session_info(user), **inventory.load_catalogue(user['id'])}


@app.get('/api/state/rows')
def get_rows(request: Request, kind: Literal['measures', 'actuals'], offset: int = 0, limit: int = 2000):
    return inventory.load_page(account(request)['id'], kind, offset, limit)


class StateUpload(BaseModel):
    batch: str = Field(min_length=8, max_length=64)
    part: int = Field(ge=0, le=10000)
    changes: dict


@app.post('/api/state/upload')
def upload_state(body: StateUpload, request: Request):
    return inventory.upload(account(request)['id'], body.batch, body.part, body.changes)


class StateChanges(BaseModel):
    revision: int = Field(ge=0)
    catalogue: dict | None = None
    changes: dict = {}
    order: dict = {}
    batch: str | None = Field(default=None, min_length=8, max_length=64)
    parts: int = Field(default=0, ge=0, le=10000)


@app.post('/api/state/changes')
def save_changes(body: StateChanges, request: Request):
    user = account(request)
    return inventory.save(user['id'], body.revision, catalogue=body.catalogue, changes=body.changes, order=body.order,
                          batch=body.batch, parts=body.parts, on_saved=lambda s, _: event(s, user['id'], 'Datos guardados'))


class StateBody(BaseModel):
    revision: int = Field(ge=0)
    state: dict


@app.put('/api/state')
def put_state(body: StateBody, request: Request):
    user = account(request)
    return inventory.save(user['id'], body.revision, full=body.state, on_saved=lambda s, _: event(s, user['id'], 'Datos guardados'))


class ReportBody(BaseModel):
    year: int = Field(ge=1900, le=2200)
    scope: str = 'GRP'
    scopeKind: Literal['L', 'E'] = 'L'
    s2: Literal['Market-based', 'Location-based'] = 'Market-based'
    template: Literal['gen'] = 'gen'
    sections: list[str] | None = None
    useAI: bool = False
    lang: Literal['es', 'en'] = 'es'


@app.post('/api/reports')
async def generate_report(body: ReportBody, request: Request):
    user = section_user(request, 'reportes')
    row = inventory.load(user['id'])
    if not row['state']:
        raise HTTPException(422, 'Primero guardá datos')
    st = row['state']
    nodes = st['masterData']['legalEntities' if body.scopeKind == 'E' else 'locations']
    if body.scope not in {x['id'] for x in nodes} and not (body.scope == 'ALL' and body.scopeKind == 'L'):
        raise HTTPException(422, 'Alcance o método inválido')
    try:
        selected_sections(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    try:
        report = await make_report(st, body.model_dump(), user['company'])
    except (RuntimeError, ValueError, KeyError, httpx.HTTPError) as exc:
        raise HTTPException(502, 'No se pudo generar el reporte. Revisá configuración y cuota del servicio de IA.') from exc
    report['id'] = str(uuid.uuid4())
    report['meta']['dataRevision'] = row['revision']
    report['meta']['revision'] = 1
    with db() as s:
        s.execute('INSERT INTO reports VALUES (?, ?, ?, ?)',
                  (report['id'], user['id'], json.dumps(report), report['meta']['generated']))
        event(s, user['id'], 'Reporte generado: ' + report['id'])
    return report


@app.get('/api/reports')
def list_reports(request: Request):
    user = section_user(request, 'reportes')
    with db() as s:
        rows = s.execute('SELECT body FROM reports WHERE account=? ORDER BY created DESC', (user['id'],)).fetchall()
    return [json.loads(r['body']) for r in rows]


@app.put('/api/reports/{report_id}')
def approve_report(report_id: str, body: dict, request: Request):
    user = section_user(request, 'reportes')
    with db() as s:
        row = s.execute('SELECT body FROM reports WHERE id=? AND account=?', (report_id, user['id'])).fetchone()
        if not row:
            raise HTTPException(404, 'Reporte no encontrado')
        original = json.loads(row['body'])
        sections = body.get('sections')
        if not isinstance(sections, list) or len(sections) > 50:
            raise HTTPException(422, 'Secciones inválidas')
        if [s.get('id') for s in sections] != [s['id'] for s in original['sections']]:
            raise HTTPException(422, 'Las secciones no coinciden con el reporte')
        for section, baseline in zip(sections, original['sections']):
            if not isinstance(section.get('title'), str) or not isinstance(section.get('id'), str):
                raise HTTPException(422, 'Sección inválida')
            if len(section.get('blocks', [])) != len(baseline['blocks']):
                raise HTTPException(422, 'La estructura del reporte no coincide')
            for block, old in zip(section.get('blocks', []), baseline['blocks']):
                if block.get('type') != old['type']:
                    raise HTTPException(422, 'Contenido inválido')
                if old['type'] in ('p', 'h3'):
                    if not isinstance(block.get('text'), str) or len(block['text']) > 30000:
                        raise HTTPException(422, 'Texto inválido')
                elif block != old:
                    raise HTTPException(422, 'Los datos de gráficos y tablas no son editables')
        if body.get('meta', {}).get('revision') != original['meta'].get('revision'):
            raise HTTPException(409, 'El reporte cambió. Volvé a abrirlo desde el historial.')
        original['sections'] = sections
        original['meta']['status'] = 'Approved' if body.get('meta', {}).get('status') == 'Approved' else 'Draft'
        if original['meta']['status'] == 'Approved':
            original['meta']['approvedAt'] = datetime.now(timezone.utc).isoformat()
        else:
            original['meta'].pop('approvedAt', None)
        original['meta']['revision'] = original['meta'].get('revision', 0) + 1
        result = s.execute('UPDATE reports SET body=? WHERE id=? AND account=? AND body=?', (json.dumps(original), report_id, user['id'], row['body']))
        if result.rowcount != 1:
            raise HTTPException(409, 'El reporte cambió. Volvé a abrirlo desde el historial.')
        event(s, user['id'], 'Reporte guardado (' + original['meta']['status'] + '): ' + report_id)
    return original


@app.post('/api/reports/{report_id}/sections/{section_id}/regenerate')
async def regenerate(report_id: str, section_id: str, request: Request):
    user = section_user(request, 'reportes')
    with db() as s:
        row = s.execute('SELECT body FROM reports WHERE id=? AND account=?', (report_id, user['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Reporte no encontrado')
    report = json.loads(row['body'])
    try:
        section = await regenerate_section(report, section_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(502, 'No se pudo regenerar la sección. Revisá la conexión y la cuota de IA.') from exc
    report['sections'] = [section if s['id'] == section_id else s for s in report['sections']]
    report['meta']['status'] = 'Draft'
    report['meta'].pop('approvedAt', None)
    report['meta']['revision'] = report['meta'].get('revision', 0) + 1
    with db() as s:
        result = s.execute('UPDATE reports SET body=? WHERE id=? AND account=? AND body=?', (json.dumps(report), report_id, user['id'], row['body']))
        if result.rowcount != 1:
            raise HTTPException(409, 'El reporte cambió durante la regeneración. Volvé a abrirlo.')
        event(s, user['id'], 'Sección regenerada: ' + section_id)
    return report


@app.get('/api/events')
def list_events(request: Request):
    user = account(request)
    with db() as s:
        return [dict(r) for r in s.execute('SELECT action, created FROM events WHERE account=? ORDER BY created DESC', (user['id'],)).fetchall()]


def carbon_link_row(account_id):
    with db() as s:
        return s.execute('SELECT * FROM carbon_links WHERE account=?', (account_id,)).fetchone()


class CarbonConnectBody(BaseModel):
    token: str = Field(min_length=1, max_length=300)


async def connect_carbon_account(account_id, token, by=''):
    """Validate the token against IVZ Carbon and store it; the site mapping starts empty."""
    try:
        sites = await carbon_link.fetch_sites(token)
    except httpx.HTTPError as exc:
        raise HTTPException(422, 'No se pudo validar el token de IVZ Carbon.') from exc
    with db() as s:
        s.execute('INSERT INTO carbon_links VALUES (?, ?, \'{}\', NULL, NULL) '
                  'ON CONFLICT(account) DO UPDATE SET token=?, site_map=\'{}\', last_sync=NULL, last_count=NULL',
                  (account_id, token, token))
        event(s, account_id, 'Integración IVZ Carbon conectada' + by)
    return sites


@app.post('/api/integrations/carbon/connect')
async def connect_carbon(body: CarbonConnectBody, request: Request):
    user = carbon_user(request)
    sites = await connect_carbon_account(user['id'], body.token)
    return {'connected': True, 'sites': sites, 'siteMap': {}}


@app.get('/api/integrations/carbon')
async def get_carbon_link(request: Request):
    user = carbon_user(request)
    row = carbon_link_row(user['id'])
    if not row:
        return {'connected': False}
    try:
        sites = await carbon_link.fetch_sites(row['token'])
    except httpx.HTTPError as exc:
        raise HTTPException(502, 'No se pudo contactar a IVZ Carbon. Revisá el token o intentá más tarde.') from exc
    return {'connected': True, 'sites': sites, 'siteMap': json.loads(row['site_map']),
            'lastSync': row['last_sync'], 'lastCount': row['last_count']}


class CarbonMappingBody(BaseModel):
    siteMap: dict[str, str]


@app.put('/api/integrations/carbon/mapping')
def set_carbon_mapping(body: CarbonMappingBody, request: Request):
    user = carbon_user(request)
    if not carbon_link_row(user['id']):
        raise HTTPException(404, 'Conectá IVZ Carbon primero.')
    state = inventory.load_catalogue(user['id'])['state']
    operable = {l['id'] for l in state['masterData']['locations'] if l.get('operable')} if state else set()
    if any(loc not in operable for loc in body.siteMap.values()):
        raise HTTPException(422, 'Ubicación inválida en el mapeo.')
    with db() as s:
        s.execute('UPDATE carbon_links SET site_map=? WHERE account=?', (json.dumps(body.siteMap), user['id']))
    return {'siteMap': body.siteMap}


@app.post('/api/integrations/carbon/sync')
async def sync_carbon(request: Request):
    return await sync_carbon_account(carbon_user(request)['id'])


async def sync_carbon_account(account_id, by=''):
    link = carbon_link_row(account_id)
    if not link:
        raise HTTPException(404, 'Conectá IVZ Carbon primero.')
    site_map = json.loads(link['site_map'])
    if not site_map:
        raise HTTPException(422, 'Mapeá al menos un sitio antes de sincronizar.')
    for attempt in range(5):
        row = inventory.load(account_id)
        if not row['state']:
            raise HTTPException(422, 'Primero guardá datos en el Hub.')
        state = row['state']
        try:
            closed = {c['year'] for c in inventory.closures(account_id)}
            count = await carbon_link.sync_measures(state, site_map, link['token'], closed)
        except httpx.HTTPError as exc:
            raise HTTPException(502, 'No se pudo sincronizar con IVZ Carbon. Revisá el token o intentá más tarde.') from exc
        updated = datetime.now(timezone.utc).isoformat()

        def saved(s, _):
            s.execute('UPDATE carbon_links SET last_sync=?, last_count=? WHERE account=?', (updated, count, account_id))
            event(s, account_id, f'IVZ Carbon sincronizado: {count} registros{by}')
        try:
            inventory.save(account_id, row['revision'], full=state, on_saved=saved)
        except HTTPException as exc:
            if exc.status_code == 409:
                continue
            raise
        return {'lastSync': updated, 'lastCount': count}
    raise HTTPException(409, 'Otra pestaña modificó los datos durante la sincronización. Reintentá.')


@app.get('/api/closures')
def list_closures(request: Request):
    return inventory.closures(account(request)['id'])


class CloseYear(BaseModel):
    year: int = Field(ge=1900, le=2200)


def acting_as(user):
    return 'Administrador de Invenzis' if user.get('impersonated_by') else user['username']


@app.post('/api/closures')
def close_year(body: CloseYear, request: Request):
    user = account(request)
    by = acting_as(user)
    return inventory.close_year(user['id'], body.year, by, lambda s: event(s, user['id'], f'Año {body.year} cerrado por {by}'))


class ReopenYear(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@app.post('/api/closures/{year}/reopen')
def reopen_year(year: int, body: ReopenYear, request: Request):
    user = account(request)
    # Only an administrator, working inside the client's account, can reopen a closed year.
    if not user.get('impersonated_by'):
        raise HTTPException(403, 'Solo un administrador puede reabrir un año cerrado.')
    return inventory.reopen_year(user['id'], year,
                                 lambda s: event(s, user['id'], f'Año {year} reabierto por {acting_as(user)}. Motivo: {body.reason.strip()}'))


def disconnect_carbon_account(account_id, by=''):
    with db() as s:
        s.execute('DELETE FROM carbon_links WHERE account=?', (account_id,))
        event(s, account_id, 'Integración IVZ Carbon desconectada' + by)


@app.delete('/api/integrations/carbon')
def disconnect_carbon(request: Request):
    disconnect_carbon_account(carbon_user(request)['id'])
    return {'ok': True}


@app.get('/api/admin/accounts')
def admin_list_accounts(request: Request):
    require_admin(request)
    with db() as s:
        rows = s.execute('SELECT a.id,a.username,a.company,a.role,a.active,a.created,st.revision,'
                         '(SELECT COUNT(*) FROM state_rows r WHERE r.account=a.id) AS records FROM accounts a '
                         'LEFT JOIN states st ON st.account=a.id ORDER BY a.created DESC, a.username').fetchall()
    return [dict(r) for r in rows]


class AdminAccountCreate(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    company: str = Field(min_length=1, max_length=200)


@app.post('/api/admin/accounts')
def admin_create_account(body: AdminAccountCreate, request: Request):
    admin = require_admin(request)
    name = body.username.strip().lower()
    company = body.company.strip()
    password = secrets.token_urlsafe(12)
    account_id = str(uuid.uuid4())
    with db() as s:
        if s.execute('SELECT id FROM accounts WHERE username=?', (name,)).fetchone():
            raise HTTPException(409, 'Ya existe una cuenta con ese usuario.')
        s.execute('INSERT INTO accounts (id,username,company,password,role,active,created) VALUES (?,?,?,?,?,?,?)',
                  (account_id, name, company, hash_password(password), 'client', True, datetime.now(timezone.utc).isoformat()))
        event(s, admin['id'], f'Cuenta creada: {name} ({company})')
    return {'id': account_id, 'username': name, 'company': company, 'password': password}


@app.post('/api/admin/accounts/{account_id}/reset-password')
def admin_reset_password(account_id: str, request: Request):
    admin = require_admin(request)
    password = secrets.token_urlsafe(12)
    with db() as s:
        target = s.execute('SELECT id,username FROM accounts WHERE id=?', (account_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'Cuenta no encontrada.')
        s.execute('UPDATE accounts SET password=? WHERE id=?', (hash_password(password), account_id))
        s.execute('DELETE FROM sessions WHERE account=?', (account_id,))
        event(s, admin['id'], f'Contraseña reseteada: {target["username"]}')
    return {'password': password}


class AdminSetActive(BaseModel):
    active: bool


@app.put('/api/admin/accounts/{account_id}/active')
def admin_set_active(account_id: str, body: AdminSetActive, request: Request):
    admin = require_admin(request)
    if account_id == admin['id'] and not body.active:
        raise HTTPException(400, 'No podés desactivar tu propia cuenta de administrador.')
    with db() as s:
        target = s.execute('SELECT id,username FROM accounts WHERE id=?', (account_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'Cuenta no encontrada.')
        s.execute('UPDATE accounts SET active=? WHERE id=?', (body.active, account_id))
        if not body.active:
            s.execute('DELETE FROM sessions WHERE account=?', (account_id,))
        event(s, admin['id'], ('Cuenta desactivada: ' if not body.active else 'Cuenta reactivada: ') + target['username'])
    return {'ok': True}


def admin_target(s, account_id):
    target = s.execute('SELECT id,username,role FROM accounts WHERE id=?', (account_id,)).fetchone()
    if not target:
        raise HTTPException(404, 'Cuenta no encontrada.')
    return target


def admin_carbon_status(account_id):
    link = carbon_link_row(account_id)
    if not link:
        return {'connected': False}
    return {'connected': True, 'mapped': len(json.loads(link['site_map'])), 'lastSync': link['last_sync'], 'lastCount': link['last_count']}


@app.get('/api/admin/accounts/{account_id}/settings')
def admin_get_settings(account_id: str, request: Request):
    require_admin(request)
    with db() as s:
        admin_target(s, account_id)
        current = features.of(s, account_id)
    return {**features.catalogue(current), 'carbon': admin_carbon_status(account_id)}


class AdminSettings(BaseModel):
    sections: dict[str, bool] = {}
    integrations: dict[str, bool] = {}


@app.put('/api/admin/accounts/{account_id}/settings')
def admin_put_settings(account_id: str, body: AdminSettings, request: Request):
    admin = require_admin(request)
    with db() as s:
        target = admin_target(s, account_id)
        current = features.update(s, account_id, body.model_dump())
        changed = ', '.join(f'{k} {"activada" if on else "desactivada"}' for kind in ('sections', 'integrations') for k, on in getattr(body, kind).items())
        event(s, admin['id'], f'Configuración de {target["username"]}: {changed}')
    return features.catalogue(current)


def admin_carbon_target(request, account_id):
    admin = require_admin(request)
    with db() as s:
        target = admin_target(s, account_id)
        features.require(s, account_id, 'integrations', 'INT-IVZC', 'Activá primero la integración con IVZ Carbon.')
    return admin, target


@app.post('/api/admin/accounts/{account_id}/carbon')
async def admin_connect_carbon(account_id: str, body: CarbonConnectBody, request: Request):
    admin, target = admin_carbon_target(request, account_id)
    await connect_carbon_account(account_id, body.token, ' por el administrador')
    with db() as s:
        event(s, admin['id'], f'IVZ Carbon conectado para {target["username"]}')
    return admin_carbon_status(account_id)


@app.post('/api/admin/accounts/{account_id}/carbon/sync')
async def admin_sync_carbon(account_id: str, request: Request):
    admin_carbon_target(request, account_id)
    await sync_carbon_account(account_id, ' por el administrador')
    return admin_carbon_status(account_id)


@app.delete('/api/admin/accounts/{account_id}/carbon')
def admin_disconnect_carbon(account_id: str, request: Request):
    admin = require_admin(request)
    with db() as s:
        target = admin_target(s, account_id)
    disconnect_carbon_account(account_id, ' por el administrador')
    with db() as s:
        event(s, admin['id'], f'IVZ Carbon desconectado para {target["username"]}')
    return admin_carbon_status(account_id)


@app.post('/api/admin/accounts/{account_id}/impersonate')
def admin_impersonate(account_id: str, request: Request, response: Response):
    admin = require_admin(request)
    with db() as s:
        target = s.execute('SELECT id,username,active FROM accounts WHERE id=?', (account_id,)).fetchone()
        if not target or not target['active']:
            raise HTTPException(404, 'Cuenta no encontrada o inactiva.')
        token = secrets.token_urlsafe(32)
        s.execute('INSERT INTO sessions (token,account,expires,impersonated_by) VALUES (?,?,?,?)',
                  (token_hash(token), account_id, time.time()+28800, admin['id']))
        event(s, admin['id'], f'Entró como: {target["username"]}')
    secure = bool(os.getenv('VERCEL')) or os.getenv('COOKIE_SECURE') == '1'
    response.set_cookie('ivz_admin_return', request.cookies.get('ivz_session', ''),
                        httponly=True, samesite='strict', secure=secure, max_age=28800, path='/')
    response.set_cookie('ivz_session', token, httponly=True, samesite='strict', secure=secure, max_age=28800, path='/')
    return {'ok': True}


@app.post('/api/admin/return')
def admin_return(request: Request, response: Response):
    return_token = request.cookies.get('ivz_admin_return', '')
    if not return_token:
        raise HTTPException(400, 'No hay una sesión de administrador para volver.')
    with db() as s:
        row = s.execute('SELECT a.id,a.role FROM accounts a JOIN sessions t ON t.account=a.id WHERE t.token=? AND t.expires>?',
                        (token_hash(return_token), time.time())).fetchone()
    if not row or row['role'] != 'admin':
        response.delete_cookie('ivz_admin_return', path='/')
        raise HTTPException(401, 'La sesión de administrador venció. Volvé a ingresar.')
    secure = bool(os.getenv('VERCEL')) or os.getenv('COOKIE_SECURE') == '1'
    response.set_cookie('ivz_session', return_token, httponly=True, samesite='strict', secure=secure, max_age=28800, path='/')
    response.delete_cookie('ivz_admin_return', path='/')
    return {'ok': True}


@app.get('/')
def index():
    return FileResponse(ROOT / 'index.html', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/admin')
def admin_page(request: Request):
    try:
        user = account(request)
    except HTTPException:
        return FileResponse(ROOT / 'index.html', headers={'Cache-Control': 'no-store, max-age=0'})
    if user['role'] != 'admin':
        raise HTTPException(403, 'Necesitás permisos de administrador.')
    return FileResponse(ROOT / 'admin.html', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/bridge.js')
def bridge():
    return FileResponse(ROOT / 'bridge.js', media_type='text/javascript', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/reports-ui.js')
def reports_ui():
    return FileResponse(ROOT / 'reports-ui.js', media_type='text/javascript', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/carbon-integration-ui.js')
def carbon_integration_ui():
    return FileResponse(ROOT / 'carbon-integration-ui.js', media_type='text/javascript', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/globe.js')
def globe_js():
    return FileResponse(ROOT / 'globe.js', media_type='text/javascript', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/map-ui.js')
def map_ui():
    return FileResponse(ROOT / 'map-ui.js', media_type='text/javascript', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/world.js')
def world_js():
    # Static country outlines; the page requests it with a version query, so it can be cached.
    return FileResponse(ROOT / 'world.js', media_type='text/javascript', headers={'Cache-Control': 'public, max-age=86400'})


import httpx

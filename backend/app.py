import hmac
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
from .metrics import validate_state
from .reports import make_report, regenerate_section, selected_sections
from . import carbon_link, mfa
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
DUMMY_PASSWORD = hash_password('dummy-not-an-account')
# How long a login (password + email code) lasts. 30 days for now; set SESSION_HOURS=8 once licenses are sold.
SESSION_TTL = int(float(os.getenv('SESSION_HOURS', '720')) * 3600)


def bootstrap_admin():
    """Provision the ADMIN account from secrets; never reset an existing user's password or email."""
    digest, email = os.getenv('ADMIN_PASSWORD_HASH'), os.getenv('ADMIN_EMAIL', '').strip()
    with db() as s:
        if digest:
            s.execute("INSERT INTO accounts (id,username,company,password,role,active,created) VALUES (?,?,?,?,?,?,?) "
                      "ON CONFLICT(username) DO NOTHING",
                      (str(uuid.uuid4()), 'admin', 'Administración IVZ Sustainability Hub', digest, 'admin', True,
                       datetime.now(timezone.utc).isoformat()))
        if email:
            # The admin also logs in with an email code, so it needs an address before its first login.
            s.execute("UPDATE accounts SET email=? WHERE username='admin' AND (email IS NULL OR email='')", (email,))


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
    if not user['email']:
        raise HTTPException(403, 'Tu cuenta no tiene un correo para recibir el código de verificación. Pedile al administrador que lo cargue.')
    # The password alone never opens a session: it only starts an email code challenge.
    # login_limits is kept until the code is verified, so it also caps how many challenges an attacker gets.
    challenge, code = secrets.token_urlsafe(32), mfa.new_code()
    with db() as s:
        s.execute('DELETE FROM login_challenges WHERE expires<? OR account=?', (now, user['id']))
        s.execute('INSERT INTO login_challenges (token,account,code,expires,sent_at) VALUES (?,?,?,?,?)',
                  (token_hash(challenge), user['id'], mfa.code_digest(challenge, code), now+mfa.CODE_TTL, now))
    mfa.send_code(user['email'], code)
    response.set_cookie('ivz_mfa', challenge, httponly=True, secure=secure_cookies(), samesite='strict', max_age=mfa.CODE_TTL, path='/')
    return {'mfa': True, 'email': mfa.mask(user['email'])}


def secure_cookies():
    return bool(os.getenv('VERCEL')) or os.getenv('COOKIE_SECURE') == '1'


CHALLENGE_GONE = 'El código venció o se usó demasiadas veces. Volvé a ingresar tu usuario y contraseña.'


class LoginCode(BaseModel):
    code: str = Field(min_length=1, max_length=20)


@app.post('/api/login/verify')
def login_verify(body: LoginCode, request: Request, response: Response):
    challenge, now = request.cookies.get('ivz_mfa', ''), time.time()
    with db() as s:
        # Count the attempt before comparing, atomically, so parallel guesses can't exceed the limit.
        row = s.execute('UPDATE login_challenges SET attempts=attempts+1 WHERE token=? AND expires>? RETURNING account,code,attempts',
                        (token_hash(challenge), now)).fetchone()
    if not row or row['attempts'] > mfa.MAX_ATTEMPTS:
        raise HTTPException(410, CHALLENGE_GONE)
    if not hmac.compare_digest(row['code'], mfa.code_digest(challenge, mfa.clean_code(body.code))):
        left = mfa.MAX_ATTEMPTS - row['attempts']
        if not left:
            raise HTTPException(410, CHALLENGE_GONE)
        raise HTTPException(401, f'Código incorrecto. Te quedan {left} intento{"s" if left > 1 else ""}.')
    token = secrets.token_urlsafe(32)
    with db() as s:
        # Single use: a code that raced another request is rejected by the DELETE count.
        used = s.execute('DELETE FROM login_challenges WHERE token=?', (token_hash(challenge),)).rowcount
        user = s.execute('SELECT id,username,company,active FROM accounts WHERE id=?', (row['account'],)).fetchone()
        if used and user and user['active']:
            s.execute('DELETE FROM sessions WHERE expires<?', (now,))
            s.execute('INSERT INTO sessions (token,account,expires) VALUES (?, ?, ?)', (token_hash(token), user['id'], now+SESSION_TTL))
            s.execute('DELETE FROM login_limits WHERE username=?', (user['username'],))
    response.delete_cookie('ivz_mfa', path='/')
    if not used:
        raise HTTPException(410, CHALLENGE_GONE)
    if not user or not user['active']:
        raise HTTPException(403, 'Esta cuenta fue desactivada.')
    response.set_cookie('ivz_session', token, httponly=True, secure=secure_cookies(), samesite='strict', max_age=SESSION_TTL, path='/')
    return {'company': user['company']}


@app.post('/api/login/resend')
def login_resend(request: Request):
    challenge, now = request.cookies.get('ivz_mfa', ''), time.time()
    code = mfa.new_code()
    with db() as s:
        row = s.execute('SELECT c.sends,c.sent_at,a.email FROM login_challenges c JOIN accounts a ON a.id=c.account '
                        'WHERE c.token=? AND c.expires>? AND c.attempts<?', (token_hash(challenge), now, mfa.MAX_ATTEMPTS)).fetchone()
        if not row or not row['email']:
            raise HTTPException(410, CHALLENGE_GONE)
        if row['sends'] >= mfa.MAX_SENDS:
            raise HTTPException(429, 'Ya te reenviamos el código varias veces. Volvé a ingresar tu usuario y contraseña.')
        wait = int(row['sent_at'] + mfa.RESEND_AFTER - now) + 1
        # Conditional update so two quick clicks can't both send.
        sent = s.execute('UPDATE login_challenges SET code=?,sends=sends+1,sent_at=?,expires=? WHERE token=? AND sent_at<=?',
                         (mfa.code_digest(challenge, code), now, now+mfa.CODE_TTL, token_hash(challenge), now-mfa.RESEND_AFTER)).rowcount
    if not sent:
        raise HTTPException(429, f'Esperá {max(wait, 1)} segundos para pedir otro código.')
    mfa.send_code(row['email'], code)
    return {'email': mfa.mask(row['email'])}


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
            'role': user['role'], 'impersonating': bool(user.get('impersonated_by'))}


@app.get('/api/state')
def get_state(request: Request):
    user = account(request)
    with db() as s:
        row = s.execute('SELECT * FROM states WHERE account=?', (user['id'],)).fetchone()
    return {'company': user['company'], 'username': user['username'], 'revision': row['revision'] if row else 0,
            'state': json.loads(row['body']) if row else None, 'ai': bool(os.getenv('GEMINI_API_KEY')),
            'role': user['role'], 'impersonating': bool(user.get('impersonated_by'))}


class StateBody(BaseModel):
    revision: int = Field(ge=0)
    state: dict


@app.put('/api/state')
def put_state(body: StateBody, request: Request):
    user = account(request)
    try:
        validate_state(body.state)
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise HTTPException(422, 'Datos inválidos: ' + str(exc))
    encoded = json.dumps(body.state, allow_nan=False)
    with db() as s:
        if body.revision == 0:
            result = s.execute('INSERT INTO states VALUES (?, 1, ?) ON CONFLICT(account) DO NOTHING', (user['id'], encoded))
        else:
            result = s.execute('UPDATE states SET revision=revision+1, body=? WHERE account=? AND revision=?',
                               (encoded, user['id'], body.revision))
        if result.rowcount != 1:
            raise HTTPException(409, 'Otra pestaña modificó los datos. Recargá antes de continuar.')
        event(s, user['id'], 'Datos guardados')
    return {'revision': body.revision+1}


class ReportBody(BaseModel):
    year: int = Field(ge=1900, le=2200)
    scope: str = 'GRP'
    scopeKind: Literal['L', 'E'] = 'L'
    s2: Literal['Market-based', 'Location-based'] = 'Market-based'
    template: Literal['gen'] = 'gen'
    sections: list[str] | None = None
    useAI: bool = False


@app.post('/api/reports')
async def generate_report(body: ReportBody, request: Request):
    user = account(request)
    with db() as s:
        row = s.execute('SELECT body, revision FROM states WHERE account=?', (user['id'],)).fetchone()
    if not row:
        raise HTTPException(422, 'Primero guardá datos')
    st = json.loads(row['body'])
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
    user = account(request)
    with db() as s:
        rows = s.execute('SELECT body FROM reports WHERE account=? ORDER BY created DESC', (user['id'],)).fetchall()
    return [json.loads(r['body']) for r in rows]


@app.put('/api/reports/{report_id}')
def approve_report(report_id: str, body: dict, request: Request):
    user = account(request)
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
    user = account(request)
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


@app.post('/api/integrations/carbon/connect')
async def connect_carbon(body: CarbonConnectBody, request: Request):
    user = account(request)
    try:
        sites = await carbon_link.fetch_sites(body.token)
    except httpx.HTTPError as exc:
        raise HTTPException(422, 'No se pudo validar el token de IVZ Carbon.') from exc
    with db() as s:
        s.execute('INSERT INTO carbon_links VALUES (?, ?, \'{}\', NULL, NULL) '
                  'ON CONFLICT(account) DO UPDATE SET token=?, site_map=\'{}\', last_sync=NULL, last_count=NULL',
                  (user['id'], body.token, body.token))
        event(s, user['id'], 'Integración IVZ Carbon conectada')
    return {'connected': True, 'sites': sites, 'siteMap': {}}


@app.get('/api/integrations/carbon')
async def get_carbon_link(request: Request):
    user = account(request)
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
    user = account(request)
    if not carbon_link_row(user['id']):
        raise HTTPException(404, 'Conectá IVZ Carbon primero.')
    with db() as s:
        row = s.execute('SELECT body FROM states WHERE account=?', (user['id'],)).fetchone()
        operable = {l['id'] for l in json.loads(row['body'])['masterData']['locations'] if l.get('operable')} if row else set()
        if any(loc not in operable for loc in body.siteMap.values()):
            raise HTTPException(422, 'Ubicación inválida en el mapeo.')
        s.execute('UPDATE carbon_links SET site_map=? WHERE account=?', (json.dumps(body.siteMap), user['id']))
    return {'siteMap': body.siteMap}


@app.post('/api/integrations/carbon/sync')
async def sync_carbon(request: Request):
    user = account(request)
    link = carbon_link_row(user['id'])
    if not link:
        raise HTTPException(404, 'Conectá IVZ Carbon primero.')
    site_map = json.loads(link['site_map'])
    if not site_map:
        raise HTTPException(422, 'Mapeá al menos un sitio antes de sincronizar.')
    for attempt in range(5):
        with db() as s:
            row = s.execute('SELECT revision, body FROM states WHERE account=?', (user['id'],)).fetchone()
        if not row:
            raise HTTPException(422, 'Primero guardá datos en el Hub.')
        state = json.loads(row['body'])
        try:
            count = await carbon_link.sync_measures(state, site_map, link['token'])
        except httpx.HTTPError as exc:
            raise HTTPException(502, 'No se pudo sincronizar con IVZ Carbon. Revisá el token o intentá más tarde.') from exc
        updated = datetime.now(timezone.utc).isoformat()
        with db() as s:
            result = s.execute('UPDATE states SET revision=revision+1, body=? WHERE account=? AND revision=?',
                               (json.dumps(state, allow_nan=False), user['id'], row['revision']))
            if result.rowcount == 1:
                s.execute('UPDATE carbon_links SET last_sync=?, last_count=? WHERE account=?', (updated, count, user['id']))
                event(s, user['id'], f'IVZ Carbon sincronizado: {count} registros')
                return {'lastSync': updated, 'lastCount': count}
    raise HTTPException(409, 'Otra pestaña modificó los datos durante la sincronización. Reintentá.')


@app.delete('/api/integrations/carbon')
def disconnect_carbon(request: Request):
    user = account(request)
    with db() as s:
        s.execute('DELETE FROM carbon_links WHERE account=?', (user['id'],))
        event(s, user['id'], 'Integración IVZ Carbon desconectada')
    return {'ok': True}


@app.get('/api/admin/accounts')
def admin_list_accounts(request: Request):
    require_admin(request)
    with db() as s:
        rows = s.execute('SELECT a.id,a.username,a.email,a.company,a.role,a.active,a.created,st.revision FROM accounts a '
                         'LEFT JOIN states st ON st.account=a.id ORDER BY a.created DESC, a.username').fetchall()
    return [dict(r) for r in rows]


class AdminAccountCreate(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    company: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=1, max_length=254)


@app.post('/api/admin/accounts')
def admin_create_account(body: AdminAccountCreate, request: Request):
    admin = require_admin(request)
    name = body.username.strip().lower()
    company = body.company.strip()
    email = mfa.clean_email(body.email)
    password = secrets.token_urlsafe(12)
    account_id = str(uuid.uuid4())
    with db() as s:
        if s.execute('SELECT id FROM accounts WHERE username=?', (name,)).fetchone():
            raise HTTPException(409, 'Ya existe una cuenta con ese usuario.')
        s.execute('INSERT INTO accounts (id,username,company,password,role,active,created,email) VALUES (?,?,?,?,?,?,?,?)',
                  (account_id, name, company, hash_password(password), 'client', True, datetime.now(timezone.utc).isoformat(), email))
        event(s, admin['id'], f'Cuenta creada: {name} ({company})')
    return {'id': account_id, 'username': name, 'company': company, 'email': email, 'password': password}


class AdminSetEmail(BaseModel):
    email: str = Field(min_length=1, max_length=254)


@app.put('/api/admin/accounts/{account_id}/email')
def admin_set_email(account_id: str, body: AdminSetEmail, request: Request):
    admin = require_admin(request)
    email = mfa.clean_email(body.email)
    with db() as s:
        target = s.execute('SELECT id,username FROM accounts WHERE id=?', (account_id,)).fetchone()
        if not target:
            raise HTTPException(404, 'Cuenta no encontrada.')
        s.execute('UPDATE accounts SET email=? WHERE id=?', (email, account_id))
        event(s, admin['id'], f'Correo de verificación cambiado: {target["username"]}')
    return {'ok': True}


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
    response.set_cookie('ivz_session', return_token, httponly=True, samesite='strict', secure=secure, max_age=SESSION_TTL, path='/')
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


import httpx

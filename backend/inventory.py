"""Account data persistence.

Measures and actuals are rows in state_rows, with their order in seq; everything else (metrics,
locations, targets, dimensions...) is small and stays one JSON body in states. The screen loads
rows in pages and saves only what changed: no request has to carry the whole dataset, which
Vercel caps at 4.5 MB. Every save still validates the complete state with validate_state().
"""
import json
import time
from datetime import datetime, timezone
from fastapi import HTTPException
from .metrics import validate_state
from .storage import db

ROWS = ('measures', 'actuals')
PAGE_MAX = 5000
UPLOAD_TTL = 86400
CONFLICT = 'Otra pestaña modificó los datos. Recargá antes de continuar.'


def _dumps(value):
    return json.dumps(value, allow_nan=False)


def _select(s, user, lock):
    sql = 'SELECT revision,body FROM states WHERE account=?'
    return s.execute(sql + (' FOR UPDATE' if lock and s.postgres else ''), (user,)).fetchone()


def _keyed(items):
    """Rows keyed by id; rows from old data without a unique id get one, so they can be saved one by one."""
    seen = set()
    for item in items:
        if not isinstance(item.get('id'), str) or not item['id'] or item['id'] in seen:
            item['id'] = 'ROW-' + str(len(seen)) + '-' + str(time.time_ns())
        seen.add(item['id'])
    return items


def _catalogue(s, user, lock=False):
    """Return (row, body without rows), moving a single-body state into rows the first time it is read."""
    row = _select(s, user, lock)
    if not row:
        return None, None
    body = json.loads(row['body'])
    if any(kind in body for kind in ROWS):
        if not lock:
            return _catalogue(s, user, True)
        s.execute('INSERT INTO state_backups (account,created,revision,body) VALUES (?,?,?,?)',
                  (user, datetime.now(timezone.utc).isoformat(), row['revision'], row['body']))
        s.execute('DELETE FROM state_rows WHERE account=?', (user,))
        for kind in ROWS:
            _insert(s, user, kind, list(enumerate(_keyed(body.pop(kind, None) or []))))
        s.execute('UPDATE states SET body=? WHERE account=?', (_dumps(body), user))
    return row, body


def _rows(s, user, kind, offset=0, limit=None):
    """[(seq, item)] in stored order."""
    sql = 'SELECT seq,body FROM state_rows WHERE account=? AND kind=? ORDER BY seq,id'
    args = [user, kind]
    if limit is not None:
        sql += ' LIMIT ? OFFSET ?'
        args += [limit, offset]
    return [(r['seq'], json.loads(r['body'])) for r in s.execute(sql, tuple(args)).fetchall()]


def _insert(s, user, kind, rows):
    s.executemany('INSERT INTO state_rows (account,kind,id,seq,year,body) VALUES (?,?,?,?,?,?)',
                  [(user, kind, item['id'], seq, item.get('y') if isinstance(item.get('y'), int) else None, _dumps(item))
                   for seq, item in rows])


def _delete(s, user, kind, ids):
    s.executemany('DELETE FROM state_rows WHERE account=? AND kind=? AND id=?', [(user, kind, i) for i in ids])


def load(user):
    """The complete state, as the single-body format stored it: {'revision', 'state'}."""
    with db() as s:
        row, body = _catalogue(s, user)
        if not row:
            return {'revision': 0, 'state': None}
        state = dict(body, **{kind: [item for _, item in _rows(s, user, kind)] for kind in ROWS})
    return {'revision': row['revision'], 'state': state}


def load_catalogue(user):
    with db() as s:
        row, body = _catalogue(s, user)
        if not row:
            return {'revision': 0, 'state': None, 'counts': {}}
        counts = {kind: s.execute('SELECT COUNT(*) AS n FROM state_rows WHERE account=? AND kind=?', (user, kind)).fetchone()['n']
                  for kind in ROWS}
    return {'revision': row['revision'], 'state': body, 'counts': counts}


def load_page(user, kind, offset, limit):
    if kind not in ROWS:
        raise HTTPException(422, 'Tipo de dato desconocido.')
    with db() as s:
        row, _ = _catalogue(s, user)
        if not row:
            raise HTTPException(404, 'Todavía no hay datos guardados.')
        items = [item for _, item in _rows(s, user, kind, max(0, offset), max(1, min(limit, PAGE_MAX)))]
    return {'revision': row['revision'], 'items': items}


def _check_changes(changes, staged=False):
    if not isinstance(changes, dict) or set(changes) - set(ROWS):
        raise HTTPException(422, 'Cambios inválidos.')
    for change in changes.values():
        if not isinstance(change, dict) or set(change) - ({'upsert'} if staged else {'upsert', 'delete'}):
            raise HTTPException(422, 'Cambios inválidos.')
        if not isinstance(change.get('upsert', []), list) or not isinstance(change.get('delete', []), list):
            raise HTTPException(422, 'Cambios inválidos.')
        if any(not isinstance(x, dict) or not isinstance(x.get('id'), str) or not x['id'] for x in change.get('upsert', [])):
            raise HTTPException(422, 'Registro sin identificador.')
        if any(not isinstance(x, str) for x in change.get('delete', [])):
            raise HTTPException(422, 'Identificador inválido.')


def upload(user, batch, part, changes):
    """Stage part of a change set too large for one request; the save that names the batch applies it."""
    _check_changes(changes, staged=True)
    with db() as s:
        s.execute('DELETE FROM state_uploads WHERE created<?', (time.time() - UPLOAD_TTL,))
        s.execute('DELETE FROM state_uploads WHERE account=? AND batch=? AND part=?', (user, batch, part))
        s.execute('INSERT INTO state_uploads (account,batch,part,created,body) VALUES (?,?,?,?,?)',
                  (user, batch, part, time.time(), _dumps(changes)))
    return {'ok': True}


def _merge(old, change, order):
    """Apply upserts (replace in place, new ones at the end) and deletions; then an explicit order if given."""
    new = {}
    for item in change.get('upsert', []):
        new[item['id']] = item
    deleted = set(change.get('delete', [])) - set(new)
    result = [new.pop(item['id'], item) for _, item in old if item['id'] not in deleted]
    result += new.values()
    if order is not None:
        ids = [item['id'] for item in result]
        if not isinstance(order, list) or len(order) != len(ids) or set(order) != set(ids):
            raise ValueError('Orden de registros inválido.')
        position = {k: i for i, k in enumerate(order)}
        result.sort(key=lambda item: position[item['id']])
    return result


def _sync(s, user, kind, old, new):
    """Write only the rows whose content or position changed; returns how many were written or removed."""
    before = {item['id']: (seq, item) for seq, item in old}
    survivors = [before[item['id']][0] for item in new if item['id'] in before]
    in_order = all(a < b for a, b in zip(survivors, survivors[1:])) and all(item['id'] in before for item in new[:len(survivors)])
    if in_order:
        top = max([seq for seq, _ in old], default=-1)
        seqs, tail = [], 0
        for item in new:
            if item['id'] in before:
                seqs.append(before[item['id']][0])
            else:
                tail += 1
                seqs.append(top + tail)
    else:
        seqs = list(range(len(new)))
    keep = {item['id'] for item in new}
    removed = [k for k in before if k not in keep]
    changed = [(seq, item) for seq, item in zip(seqs, new) if before.get(item['id']) != (seq, item)]
    _delete(s, user, kind, removed + [item['id'] for _, item in changed if item['id'] in before])
    _insert(s, user, kind, changed)
    return len(changed) + len(removed)


def save(user, revision, full=None, catalogue=None, changes=None, order=None, batch=None, parts=0, on_saved=None):
    """Save a complete state (full) or a change set, validated as a whole. Returns the new revision.

    on_saved(s, revision) runs inside the same transaction, e.g. to log an event."""
    changes = changes or {}
    order = order or {}
    if full is None:
        _check_changes(changes)
        if not isinstance(order, dict) or set(order) - set(ROWS):
            raise HTTPException(422, 'Orden de registros inválido.')
    with db() as s:
        row, body = _catalogue(s, user, lock=True)
        current = row['revision'] if row else 0
        if revision != current:
            raise HTTPException(409, CONFLICT)
        old = {kind: _rows(s, user, kind) for kind in ROWS}
        if batch:
            staged = s.execute('SELECT body FROM state_uploads WHERE account=? AND batch=? ORDER BY part', (user, batch)).fetchall()
            s.execute('DELETE FROM state_uploads WHERE account=? AND batch=?', (user, batch))
            if len(staged) != parts:
                raise HTTPException(409, 'El guardado quedó incompleto. Reintentá.')
            for kind in ROWS:
                upserts = [x for r in staged for x in json.loads(r['body']).get(kind, {}).get('upsert', [])]
                if upserts:
                    change = changes.setdefault(kind, {})
                    change['upsert'] = upserts + change.get('upsert', [])
        try:
            if full is not None:
                state = dict(full)
                for kind in ROWS:
                    if not isinstance(state.get(kind), list) or any(not isinstance(x, dict) for x in state[kind]):
                        raise ValueError('Falta colección: ' + kind)
                    state[kind] = _keyed([dict(x) for x in state[kind]])
            else:
                if catalogue is None and body is None:
                    raise ValueError('Todavía no hay datos guardados.')
                state = dict(catalogue if catalogue is not None else body)
                for kind in ROWS:
                    state[kind] = _merge(old[kind], changes.get(kind, {}), order.get(kind))
            validate_state(state)
            new_body = _dumps({k: v for k, v in state.items() if k not in ROWS})
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, 'Datos inválidos: ' + str(exc)) from exc
        if row:
            s.execute('UPDATE states SET revision=revision+1, body=? WHERE account=?', (new_body, user))
        elif s.execute('INSERT INTO states VALUES (?, 1, ?) ON CONFLICT(account) DO NOTHING', (user, new_body)).rowcount != 1:
            raise HTTPException(409, CONFLICT)
        for kind in ROWS:
            _sync(s, user, kind, old[kind], state[kind])
        if on_saved:
            on_saved(s, current + 1)
    return {'revision': current + 1}

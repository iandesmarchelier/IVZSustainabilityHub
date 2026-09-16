"""Clear only the local demo data after taking a consistent SQLite backup."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    if os.getenv('DATABASE_URL') or os.getenv('VERCEL'):
        raise SystemExit('Sólo disponible para la cuenta demo de SQLite local.')
    path = Path(os.getenv('SQLITE_PATH', 'data/ivz.sqlite3'))
    backup = path.with_name('ivz-antes-de-vaciar-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite3')
    with sqlite3.connect(path) as conn:
        with sqlite3.connect(backup) as copy:
            conn.backup(copy)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT body FROM states WHERE account=?', ('demo-local',)).fetchone()
        if not row:
            raise SystemExit('No hay estado de la cuenta demo para vaciar.')
        state = json.loads(row[0])
        for key in ('measures', 'actuals', 'targets', 'qualitative', 'imports', 'auditLog'):
            state[key] = []
        state['report'] = None
        for item in state['integrations']:
            item['records'] = 0
            item['lastSync'] = '—'
        conn.execute('UPDATE states SET body=?, revision=revision+1 WHERE account=?', (json.dumps(state), 'demo-local'))
        conn.execute('DELETE FROM reports WHERE account=?', ('demo-local',))
        conn.execute('DELETE FROM events WHERE account=?', ('demo-local',))
        conn.commit()
        counts = {k: len(state[k]) for k in ('measures', 'actuals', 'targets', 'qualitative', 'imports')}
        print(json.dumps({'backup': str(backup.resolve()), 'counts': counts,
                          'reports': conn.execute('SELECT COUNT(*) FROM reports WHERE account=?', ('demo-local',)).fetchone()[0]}))


if __name__ == '__main__':
    main()

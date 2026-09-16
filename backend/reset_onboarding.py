"""Explicit local demo reset with backup; does not change login credentials."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from .metrics import validate_state


def main():
    state = json.loads(Path('data/empty-state.json').read_text(encoding='utf8'))
    validate_state(state)
    backup = 'data/ivz-before-onboarding-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite3'
    with sqlite3.connect('data/ivz.sqlite3') as conn:
        with sqlite3.connect(backup) as copy:
            conn.backup(copy)
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE states SET body=?, revision=revision+1 WHERE account=?', (json.dumps(state), 'demo-local'))
        conn.execute('DELETE FROM reports WHERE account=?', ('demo-local',))
        conn.execute('DELETE FROM events WHERE account=?', ('demo-local',))
    print('Cuenta reiniciada. Respaldo:', backup)


if __name__ == '__main__':
    main()

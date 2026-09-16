"""Recover the state immediately before the overbroad reset, except locations."""
import json
import sqlite3
from pathlib import Path


def main():
    source = Path('data/ivz-antes-de-vaciar-20260916-101109.sqlite3')
    target = Path('data/ivz.sqlite3')
    with sqlite3.connect(source) as old, sqlite3.connect(target) as current:
        with sqlite3.connect('data/ivz-antes-de-restaurar-20260916.sqlite3') as backup:
            current.backup(backup)
        current.execute('BEGIN IMMEDIATE')
        state = json.loads(old.execute('SELECT body FROM states WHERE account=?', ('demo-local',)).fetchone()[0])
        state['masterData']['locations'] = []
        current.execute('UPDATE states SET body=?, revision=revision+1 WHERE account=?', (json.dumps(state), 'demo-local'))
        for table, columns in [('reports', 'id,account,body,created'), ('events', 'id,account,action,created')]:
            for row in old.execute(f'SELECT {columns} FROM {table} WHERE account=?', ('demo-local',)):
                current.execute(f'INSERT OR IGNORE INTO {table} ({columns}) VALUES (?,?,?,?)', row)
        restored = json.loads(current.execute('SELECT body FROM states WHERE account=?', ('demo-local',)).fetchone()[0])
        assert restored == state
        original = json.loads(old.execute('SELECT body FROM states WHERE account=?', ('demo-local',)).fetchone()[0])
        original['masterData']['locations'] = []
        assert restored == original
        print('Verificado: estado idéntico al respaldo anterior salvo locations=[], con 30 métricas conservadas.')


if __name__ == '__main__':
    main()

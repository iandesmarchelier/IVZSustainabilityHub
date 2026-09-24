"""Emergency rollback: put every account's state back into one body, with current data.

Run before deploying a version older than the row-based storage of measures and actuals:
    python -m backend.unsplit
The rows stay in state_rows too, so the current version keeps working if it is redeployed.
"""
import json
from .inventory import load
from .storage import SYSTEM, db, initialize


def main():
    initialize()
    with db(SYSTEM) as s:
        users = [r['account'] for r in s.execute('SELECT account FROM states').fetchall()]
    for user in users:
        state = load(user)['state']
        with db(user) as s:
            s.execute('UPDATE states SET body=? WHERE account=?', (json.dumps(state, allow_nan=False), user))
    print(f'{len(users)} cuentas vueltas al formato de un solo bloque.')


if __name__ == '__main__':
    main()

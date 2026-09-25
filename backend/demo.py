"""Create a local-only demo account; never a production default password."""
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from .storage import SYSTEM, db, initialize
from .security import hash_password


def main():
    if os.getenv('DATABASE_URL') or os.getenv('VERCEL'):
        raise SystemExit('Este comando sólo crea una cuenta en SQLite local.')
    initialize()
    with db(SYSTEM) as s:
        if s.execute('SELECT id FROM accounts WHERE username=?', ('demo',)).fetchone():
            print('La cuenta demo ya existe. Ver data/acceso-demo.txt.')
            return
        password = secrets.token_urlsafe(18)
        now = datetime.now(timezone.utc).isoformat()
        s.execute('INSERT INTO accounts (id,username,company,password,role,active,created) VALUES (?,?,?,?,?,?,?)',
                  ('demo-local', 'demo', 'Tenant Invenzis', hash_password(password), 'client', True, now))
        s.execute('INSERT INTO users (id,account,username,password,role,active,created) VALUES (?,?,?,?,?,?,?)',
                  ('demo-local', 'demo-local', 'demo', hash_password(password), 'admin', True, now))
    Path('data/acceso-demo.txt').write_text('Usuario: demo\nContraseña: ' + password + '\nSólo para esta demo local.\n', encoding='utf8')
    print('Cuenta local creada. Acceso en data/acceso-demo.txt (excluido de Git).')


if __name__ == '__main__':
    main()

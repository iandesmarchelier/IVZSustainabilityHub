"""Create a local-only demo account; never a production default password."""
import os
import secrets
from pathlib import Path
from .storage import db, initialize
from .security import hash_password


def main():
    if os.getenv('DATABASE_URL') or os.getenv('VERCEL'):
        raise SystemExit('Este comando sólo crea una cuenta en SQLite local.')
    initialize()
    with db() as s:
        if s.execute('SELECT id FROM accounts WHERE username=?', ('demo',)).fetchone():
            print('La cuenta demo ya existe. Ver data/acceso-demo.txt.')
            return
        password = secrets.token_urlsafe(18)
        s.execute('INSERT INTO accounts VALUES (?, ?, ?, ?)', ('demo-local', 'demo', 'Tenant Invenzis', hash_password(password)))
    Path('data/acceso-demo.txt').write_text('Usuario: demo\nContraseña: ' + password + '\nSólo para esta demo local.\n', encoding='utf8')
    print('Cuenta local creada. Acceso en data/acceso-demo.txt (excluido de Git).')


if __name__ == '__main__':
    main()

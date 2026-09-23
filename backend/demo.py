"""Create a local-only demo account; never a production default password."""
import os
import secrets
from datetime import datetime, timezone
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
        # Sin RESEND_API_KEY local, el código de verificación se imprime en la consola del servidor.
        s.execute('INSERT INTO accounts (id,username,company,password,role,active,created,email) VALUES (?,?,?,?,?,?,?,?)',
                  ('demo-local', 'demo', 'Tenant Invenzis', hash_password(password), 'client', True,
                   datetime.now(timezone.utc).isoformat(), 'demo@example.com'))
    Path('data/acceso-demo.txt').write_text('Usuario: demo\nContraseña: ' + password +
                                            '\nEl código de verificación aparece en la consola del servidor.\nSólo para esta demo local.\n', encoding='utf8')
    print('Cuenta local creada. Acceso en data/acceso-demo.txt (excluido de Git).')


if __name__ == '__main__':
    main()

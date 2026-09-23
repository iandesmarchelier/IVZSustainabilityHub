"""Run: python -m backend.manage USERNAME 'Company name' EMAIL."""
import getpass
import sys
import uuid
from datetime import datetime, timezone
from .storage import initialize, db
from .security import hash_password
from .mfa import clean_email


def main():
    if len(sys.argv) != 4:
        raise SystemExit('Uso: python -m backend.manage usuario "Empresa" correo@empresa.com')
    email = clean_email(sys.argv[3])
    password = getpass.getpass('Contraseña (mínimo 12 caracteres): ')
    if len(password) < 12 or password != getpass.getpass('Repetir contraseña: '):
        raise SystemExit('Contraseña corta o confirmación diferente.')
    initialize()
    with db() as s:
        s.execute('INSERT INTO accounts (id,username,company,password,role,active,created,email) VALUES (?,?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()), sys.argv[1].strip().lower(), sys.argv[2], hash_password(password),
                   'client', True, datetime.now(timezone.utc).isoformat(), email))
    print('Cuenta creada. Un usuario corresponde a una empresa. Ahora también se pueden crear cuentas desde /admin.')


if __name__ == '__main__':
    main()

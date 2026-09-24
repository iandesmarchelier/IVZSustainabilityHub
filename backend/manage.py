"""Run: python -m backend.manage USERNAME 'Company name'."""
import getpass
import sys
import uuid
from datetime import datetime, timezone
from .storage import SYSTEM, initialize, db
from .security import hash_password


def main():
    if len(sys.argv) != 3:
        raise SystemExit('Uso: python -m backend.manage usuario "Empresa"')
    password = getpass.getpass('Contraseña (mínimo 12 caracteres): ')
    if len(password) < 12 or password != getpass.getpass('Repetir contraseña: '):
        raise SystemExit('Contraseña corta o confirmación diferente.')
    initialize()
    with db(SYSTEM) as s:
        s.execute('INSERT INTO accounts (id,username,company,password,role,active,created) VALUES (?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()), sys.argv[1].strip().lower(), sys.argv[2], hash_password(password),
                   'client', True, datetime.now(timezone.utc).isoformat()))
    print('Cuenta creada. Un usuario corresponde a una empresa. Ahora también se pueden crear cuentas desde /admin.')


if __name__ == '__main__':
    main()

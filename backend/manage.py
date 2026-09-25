"""Run: python -m backend.manage USERNAME 'Company name'."""
import getpass
import sys
from .storage import SYSTEM, initialize, db
from .security import hash_password
from .users import create_account


def main():
    if len(sys.argv) != 3:
        raise SystemExit('Uso: python -m backend.manage usuario "Empresa"')
    password = getpass.getpass('Contraseña (mínimo 12 caracteres): ')
    if len(password) < 12 or password != getpass.getpass('Repetir contraseña: '):
        raise SystemExit('Contraseña corta o confirmación diferente.')
    initialize()
    with db(SYSTEM) as s:
        create_account(s, sys.argv[1], sys.argv[2], 'client', hash_password(password))
    print('Cuenta creada con su primer usuario, administrador de la empresa. Los demás usuarios se agregan desde /admin o desde la cuenta.')


if __name__ == '__main__':
    main()

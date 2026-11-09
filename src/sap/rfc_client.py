"""Conexión RFC al sistema SAP con los datos de config/conexion.local.yaml."""
import getpass
import os
from pathlib import Path

# El SDK de SAP busca sus DLL (incluidas las ICU) en el PATH.
# Las agregamos aquí para no depender de cómo esté configurada la terminal.
_sdk = os.environ.get("SAPNWRFC_HOME")
if _sdk:
    _lib = str(Path(_sdk) / "lib")
    if _lib.lower() not in os.environ.get("PATH", "").lower():
        os.environ["PATH"] = _lib + os.pathsep + os.environ.get("PATH", "")

import keyring  # noqa: E402
import yaml  # noqa: E402
from pyrfc import Connection, LogonError  # noqa: E402

CONFIG = Path(__file__).resolve().parents[2] / "config" / "conexion.local.yaml"
SERVICIO = "atc-automation-sap"   # nombre en el Administrador de credenciales de Windows


def cuenta_credencial() -> str:
    """Nombre de la cuenta en el Administrador de credenciales (usuario@host/mandante)."""
    params = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return f"{params['user']}@{params['ashost']}/{params['client']}"


def get_connection() -> Connection:
    """Abre la conexión con la contraseña guardada en el Administrador de credenciales.
    La primera vez la pide por consola y la guarda; si SAP la rechaza, la borra."""
    params = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    cuenta = cuenta_credencial()
    passwd = keyring.get_password(SERVICIO, cuenta)
    guardada = passwd is not None
    if not guardada:
        passwd = getpass.getpass(f"Password SAP ({params['user']}): ")
    try:
        conn = Connection(**params, passwd=passwd)
    except LogonError:
        if guardada:
            keyring.delete_password(SERVICIO, cuenta)
            print("La contraseña guardada no es válida; se borró. Vuelve a ejecutar para ingresarla.")
        raise
    if not guardada:
        keyring.set_password(SERVICIO, cuenta, passwd)
    return conn

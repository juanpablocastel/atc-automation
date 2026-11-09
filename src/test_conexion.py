"""
Fase 0 - Prueba de conexión RFC al sistema del cliente vía SAProuter.

Usa los datos de config\\conexion.local.yaml (no se sube a git; ver
config\\conexion.example.yaml) y la contraseña guardada en el Administrador de
credenciales de Windows. Si no hay contraseña guardada, la pide y la guarda.
"""
from pyrfc import CommunicationError, LogonError

from sap.rfc_client import get_connection


def main():
    try:
        conn = get_connection()
    except LogonError as e:
        print(f"Error de logon (usuario, clave o mandante): {e}")
        return
    except CommunicationError as e:
        print(f"Error de comunicación (router string, host o puerto): {e}")
        return

    info = conn.call("RFC_SYSTEM_INFO")["RFCSI_EXPORT"]
    print("Conexión OK")
    print(f"  Sistema : {info['RFCSYSID']}")
    print(f"  Host    : {info['RFCHOST']}")
    print(f"  Release : {info['RFCSAPRL']}")
    conn.close()


if __name__ == "__main__":
    main()

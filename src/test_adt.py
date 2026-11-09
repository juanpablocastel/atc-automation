"""
Fase 0 - Prueba del túnel ADT por RFC (solo lectura, no modifica nada en SAP).

Uso:
    python src\\test_adt.py               -> solo prueba el túnel
    python src\\test_adt.py ZPROGRAMA     -> además lee el código de ese programa
"""
import sys
from urllib.parse import quote

from sap.adt import adt_request, describir_interfaz
from sap.rfc_client import get_connection


def main():
    programa = sys.argv[1].upper() if len(sys.argv) > 1 else None
    conn = get_connection()

    try:
        # 1. Discovery: la lista de servicios ADT que ofrece el sistema
        r = adt_request(conn, "GET", "/sap/bc/adt/discovery",
                        {"Accept": "application/atomsvc+xml"})
        print(f"Discovery: {r.status} {r.reason} ({len(r.body)} bytes)")

        # 2. Código fuente de un programa
        if programa:
            uri = f"/sap/bc/adt/programs/programs/{quote(programa.lower(), safe='')}/source/main"
            r = adt_request(conn, "GET", uri, {"Accept": "text/plain"})
            print(f"\nPrograma {programa}: {r.status} {r.reason}")
            if r.ok:
                lineas = r.text.splitlines()
                print(f"{len(lineas)} líneas. Primeras 15:\n")
                for n, linea in enumerate(lineas[:15], start=1):
                    print(f"{n:>4}  {linea}")
            else:
                print(r.text[:500])
    except Exception as e:
        print(f"Error llamando al túnel ADT: {e}")
        print("\nInterfaz del módulo en este sistema:")
        describir_interfaz(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

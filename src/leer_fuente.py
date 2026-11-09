"""
Descarga el código fuente de un programa o include y lo guarda en data/sources/.
Solo lectura: no modifica nada en SAP.

Uso:
    python src\\leer_fuente.py ZPROGRAMA
    python src\\leer_fuente.py ZINCLUDE include
"""
import sys
from pathlib import Path
from urllib.parse import quote

from sap.adt import adt_request
from sap.rfc_client import get_connection

DESTINO = Path(__file__).resolve().parents[1] / "data" / "sources"
RUTAS = {"programa": "programs/programs", "include": "programs/includes"}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    nombre = sys.argv[1].upper()
    tipo = sys.argv[2].lower() if len(sys.argv) > 2 else "programa"
    uri = f"/sap/bc/adt/{RUTAS[tipo]}/{quote(nombre.lower(), safe='')}/source/main"

    conn = get_connection()
    try:
        r = adt_request(conn, "GET", uri, {"Accept": "text/plain"})
        if not r.ok:
            print(f"{nombre}: {r.status} {r.reason}\n{r.text[:500]}")
            return
        DESTINO.mkdir(parents=True, exist_ok=True)
        archivo = DESTINO / f"{nombre.replace('/', '#')}.abap"
        archivo.write_text(r.text.replace("\r\n", "\n"), encoding="utf-8")
        print(f"{nombre}: {len(r.text.splitlines())} líneas guardadas en {archivo}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

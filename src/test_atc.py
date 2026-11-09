"""
Fase 1 (prueba) - Ejecuta ATC sobre un programa y lista los hallazgos.
Solo lectura: no modifica el código en SAP.

Uso:
    python src\\test_atc.py ZPROGRAMA              -> usa la variante por defecto del sistema
    python src\\test_atc.py ZPROGRAMA VARIANTE     -> usa la variante indicada
"""
import sys

from sap.adt import fetch_csrf
from sap.atc import ejecutar_atc, uri_programa, variante_por_defecto, RAW_DIR
from sap.rfc_client import get_connection


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    programa = sys.argv[1].upper()
    conn = get_connection()
    try:
        variante = sys.argv[2].upper() if len(sys.argv) > 2 else variante_por_defecto(conn)
        print(f"Variante ATC: {variante}")
        csrf = fetch_csrf(conn)
        print(f"Token CSRF: {'sí' if csrf else 'no requerido'}")

        hallazgos = ejecutar_atc(conn, uri_programa(programa), variante, csrf)
        print(f"\n{programa}: {len(hallazgos)} hallazgos\n")
        for h in hallazgos:
            linea = h.linea if h.linea is not None else "-"
            print(f"[P{h.prioridad}] {h.include} línea {linea}")
            print(f"     Check  : {h.check} ({h.check_id})")
            print(f"     Mensaje: {h.mensaje}")
            print(f"     URI    : {h.uri}\n")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
        print(f"Respuestas crudas en: {RAW_DIR}")


if __name__ == "__main__":
    main()

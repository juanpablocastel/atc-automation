"""
Fase 1 (prueba) - Obtiene los objetos relacionados, como ZDEPENDENCIAS.
Solo lectura: no modifica nada en SAP.

Uso:
    python src\\test_dependencias.py ZPROGRAMA
    python src\\test_dependencias.py ZPROGRAMA PROG 4
"""
import sys
from collections import Counter

from sap.dependencias import entorno, obtener_dependencias
from sap.rfc_client import get_connection


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    nombre = sys.argv[1].upper()
    tipo = sys.argv[2].upper() if len(sys.argv) > 2 else "PROG"
    profundidad = int(sys.argv[3]) if len(sys.argv) > 3 else 4

    conn = get_connection()
    try:
        # 1. Respuesta cruda del primer nivel, para verificar los campos
        filas = entorno(conn, tipo, nombre)
        print(f"Primer nivel crudo: {len(filas)} filas")
        if filas:
            print(f"Campos: {', '.join(filas[0].keys())}")
            print("Tipos :", dict(Counter(f.get('TYPE', '') for f in filas)))
            for f in filas[:8]:
                print(f"   {f.get('TYPE', ''):<5} {f.get('OBJECT', ''):<40} {f.get('ENCL_OBJ', '')}")

        # 2. Recorrido completo
        objetos = obtener_dependencias(conn, tipo, nombre, profundidad)
        print(f"\nObjetos Z/Y encontrados (profundidad {profundidad}): {len(objetos)}\n")
        for o in objetos:
            marca = "ATC" if o.para_atc else "   "
            print(f"  [{marca}] nivel {o.nivel}  {o.tipo:<5} {o.nombre:<40} <- {o.padre}")

        atc = [o for o in objetos if o.para_atc]
        print(f"\nPara ATC: {len(atc)} objetos")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

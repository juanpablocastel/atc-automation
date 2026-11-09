"""
Fase 1 - Revisión ATC en solo lectura.

Por cada programa: obtiene sus objetos relacionados (como ZDEPENDENCIAS),
ejecuta ATC en los que tienen código, quita duplicados y genera un Excel.
No modifica nada en SAP.

Uso:
    python src\\fase1_revision.py ZPROG1 ZPROG2
    python src\\fase1_revision.py --archivo programas.txt
    python src\\fase1_revision.py ZPROG1 --variante MI_VARIANTE --profundidad 4
"""
import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from sap.adt import fetch_csrf
from sap.atc import ejecutar_atc, variante_por_defecto
from sap.dependencias import obtener_dependencias
from sap.repositorio import resolver
from sap.rfc_client import get_connection

RAIZ = Path(__file__).resolve().parents[1]


def leer_programas(args) -> list[str]:
    nombres = list(args.programas)
    if args.archivo:
        for linea in Path(args.archivo).read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                nombres.append(linea)
    vistos, resultado = set(), []
    for n in (x.upper() for x in nombres):
        if n not in vistos:
            vistos.add(n)
            resultado.append(n)
    return resultado


def agregar(lista: list, valores) -> None:
    for v in valores:
        if v not in lista:
            lista.append(v)


def revisar(conn, programas, variante, profundidad):
    # 1. Objetos relacionados de cada programa (sin duplicados)
    objetos = {}
    for i, prog in enumerate(programas, 1):
        print(f"[{i}/{len(programas)}] Dependencias de {prog}")
        for o in obtener_dependencias(conn, "PROG", prog, profundidad):
            reg = objetos.setdefault(o.clave, {"obj": o, "origenes": [], "estado": ""})
            agregar(reg["origenes"], [prog])

    # 2. Tipo exacto y URI de los objetos con código
    con_codigo = [r for r in objetos.values() if r["obj"].para_atc]
    for r in con_codigo:
        r["tipo_adt"], r["uri"] = resolver(conn, r["obj"].tipo, r["obj"].nombre)
    contenedores = {r["obj"].nombre for r in con_codigo if r["tipo_adt"] != "PROG/I"}

    # 3. ATC; un include cuyo programa o grupo ya se revisa no se corre aparte
    csrf = fetch_csrf(conn)
    hallazgos = {}
    for r in con_codigo:
        o = r["obj"]
        if r["tipo_adt"] == "PROG/I" and o.padre in contenedores:
            r["estado"] = f"Cubierto por {o.padre}"
            continue
        print(f"   ATC {r['tipo_adt']:<8} {o.nombre}")
        try:
            lista = ejecutar_atc(conn, r["uri"], variante, csrf)
        except Exception as e:
            r["estado"] = "Error: " + str(e).splitlines()[0]
            continue
        r["estado"] = f"Revisado ({len(lista)} hallazgos)"
        for h in lista:
            clave = (h.include, h.linea, h.check_id, h.mensaje)
            reg = hallazgos.setdefault(clave, {"h": h, "origenes": []})
            agregar(reg["origenes"], r["origenes"])

    return objetos, hallazgos


def _hoja(wb, titulo, encabezados, filas, anchos):
    ws = wb.create_sheet(titulo)
    ws.append(encabezados)
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E78")
    for f in filas:
        ws.append(f)
    for i, ancho in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def generar_excel(ruta: Path, meta: dict, objetos: dict, hallazgos: dict) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    lista_h = sorted(hallazgos.values(),
                     key=lambda r: (r["h"].prioridad, r["h"].include, r["h"].linea or 0))
    _hoja(wb, "Hallazgos",
          ["Programas origen", "Objeto revisado", "Include", "Línea", "Prioridad",
           "Check", "Mensaje", "Ref. tipo", "Ref. objeto", "Categoría", "Cambio",
           "Clasificación", "Estado"],
          [[", ".join(r["origenes"]), r["h"].objeto, r["h"].include, r["h"].linea,
            int(r["h"].prioridad) if str(r["h"].prioridad).isdigit() else r["h"].prioridad,
            r["h"].check, r["h"].mensaje, r["h"].ref_tipo, r["h"].ref_objeto, r["h"].categoria,
            r["h"].cambio, "", "Pendiente"] for r in lista_h],
          [28, 30, 32, 8, 10, 50, 60, 10, 20, 30, 16, 16, 12])

    lista_o = sorted(objetos.values(), key=lambda r: (r["obj"].nivel, r["obj"].tipo, r["obj"].nombre))
    _hoja(wb, "Objetos",
          ["Programas origen", "Tipo", "Nombre", "Tipo ADT", "Nivel", "Padre", "ATC"],
          [[", ".join(r["origenes"]), r["obj"].tipo, r["obj"].nombre, r.get("tipo_adt", ""),
            r["obj"].nivel, r["obj"].padre, r["estado"] or ("-" if not r["obj"].para_atc else "")]
           for r in lista_o],
          [28, 8, 40, 10, 8, 32, 30])

    prioridades = {}
    for r in hallazgos.values():
        prioridades[r["h"].prioridad] = prioridades.get(r["h"].prioridad, 0) + 1
    filas = [["Fecha", meta["fecha"]], ["Variante ATC", meta["variante"]],
             ["Profundidad", meta["profundidad"]], ["Programas", ", ".join(meta["programas"])],
             ["Objetos encontrados", len(objetos)],
             ["Objetos con código", sum(1 for r in objetos.values() if r["obj"].para_atc)],
             ["Hallazgos (sin duplicados)", len(hallazgos)]]
    filas += [[f"Prioridad {p}", n] for p, n in sorted(prioridades.items())]
    ws = _hoja(wb, "Resumen", ["Dato", "Valor"], filas, [28, 80])
    wb.move_sheet(ws, offset=-2)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)


def guardar_json(ruta: Path, meta: dict, objetos: dict, hallazgos: dict) -> None:
    datos = {
        "meta": meta,
        "objetos": [{**asdict(r["obj"]), "origenes": r["origenes"], "tipo_adt": r.get("tipo_adt", ""),
                     "uri": r.get("uri", ""), "estado": r["estado"]} for r in objetos.values()],
        "hallazgos": [{**asdict(r["h"]), "origenes": r["origenes"]} for r in hallazgos.values()],
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Revisión ATC en solo lectura")
    ap.add_argument("programas", nargs="*")
    ap.add_argument("--archivo", help="Archivo de texto con un programa por línea")
    ap.add_argument("--variante", help="Variante ATC (por defecto, la del sistema)")
    ap.add_argument("--profundidad", type=int, default=4)
    args = ap.parse_args()

    programas = leer_programas(args)
    if not programas:
        ap.print_help()
        return

    conn = get_connection()
    try:
        variante = (args.variante or variante_por_defecto(conn)).upper()
        print(f"Variante ATC: {variante}\n")
        objetos, hallazgos = revisar(conn, programas, variante, args.profundidad)
    finally:
        conn.close()

    sello = datetime.now().strftime("%Y%m%d_%H%M")
    meta = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "variante": variante,
            "profundidad": args.profundidad, "programas": programas}
    excel = RAIZ / "output" / f"revision_atc_{sello}.xlsx"
    generar_excel(excel, meta, objetos, hallazgos)
    guardar_json(RAIZ / "data" / "revisiones" / f"revision_{sello}.json", meta, objetos, hallazgos)

    errores = [r for r in objetos.values() if r["estado"].startswith("Error")]
    print(f"\nObjetos: {len(objetos)} | Hallazgos sin duplicados: {len(hallazgos)} | Errores: {len(errores)}")
    print(f"Excel: {excel}")


if __name__ == "__main__":
    main()

"""
Fase 2 - Prepara la carpeta de trabajo de un programa para Claude Code.

Toma el resultado de la Fase 1, agrupa los hallazgos por lugar de corrección,
descarga el código de los includes afectados y escribe TAREA.md.
Solo lectura: no modifica nada en SAP.

Uso:
    python src\\preparar.py ZBT_CONTROLKIT
    python src\\preparar.py ZBT_CONTROLKIT --revision data\\revisiones\\revision_20261108_1135.json
    python src\\preparar.py ZBT_CONTROLKIT --forzar      (vuelve a descargar el código)
"""
import argparse
import json
import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
TRABAJO = RAIZ / "trabajo"
REGISTRO = TRABAJO / "_includes.json"
REF = re.compile(r"at Include (\S+) line (\d+)", re.IGNORECASE)
# "Note: 0002215424" (extensión de campos) o "see Note(s): 0002438131" (simplificaciones)
NOTA = re.compile(r"Note(?:\(s\))?:\s*0*(\d+)", re.IGNORECASE)
RANGO = {"propuesta": 3, "automatico": 2, "pseudocomentario": 1, "reporte": 0}


def cargar_yaml(nombre: str) -> dict:
    return yaml.safe_load((RAIZ / "config" / nombre).read_text(encoding="utf-8"))


def ultima_revision(programa: str) -> Path:
    for ruta in sorted((RAIZ / "data" / "revisiones").glob("revision_*.json"), reverse=True):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if programa in datos["meta"]["programas"]:
            return ruta
    raise SystemExit(f"No hay revisión de Fase 1 que incluya {programa}. Ejecuta fase1_revision.py primero.")


def regla_para(h: dict, reglas: list[dict], fuentes: dict) -> dict:
    lineas = fuentes.get(h["include"], [])
    ventana = "\n".join(lineas[max(0, (h["linea"] or 1) - 1):(h["linea"] or 1) + 5])
    for r in reglas:
        if "codigo" in r and not re.search(r["codigo"], ventana, re.IGNORECASE):
            continue
        if "check" in r and r["check"].lower() not in h["check"].lower():
            continue
        if "mensaje" in r and not re.search(r["mensaje"], h["mensaje"], re.IGNORECASE):
            continue
        return r
    return {"nombre": "Sin regla", "clasificacion": "reporte", "instruccion": ""}


def agrupar(hallazgos: list[dict], reglas: list[dict], fuentes: dict,
            prioridades: set[str]) -> list[dict]:
    """Agrupa por lugar de corrección: un hallazgo que apunta a otra línea
    ('at Include X line N') se corrige en esa línea, no donde se reporta.
    Si hay prioridades, se conservan las tareas con al menos un hallazgo de esas prioridades."""
    tareas = {}
    for h in hallazgos:
        m = REF.search(h["mensaje"])
        lugar = (m.group(1).upper(), int(m.group(2))) if m else (h["include"], h["linea"] or 0)
        regla = regla_para(h, reglas, fuentes)
        nota = NOTA.search(h["mensaje"])
        h["nota"] = regla.get("nota") or (nota.group(1) if nota else "")
        t = tareas.setdefault(lugar, {"include": lugar[0], "linea": lugar[1],
                                      "reglas": [], "hallazgos": []})
        if regla not in t["reglas"]:
            t["reglas"].append(regla)
        t["hallazgos"].append(h)

    lista = sorted((t for t in tareas.values()
                    if not prioridades or any(str(h["prioridad"]) in prioridades for h in t["hallazgos"])),
                   key=lambda t: (t["include"], t["linea"]))
    for i, t in enumerate(lista, 1):
        t["id"] = f"T{i:02d}"
        t["clasificacion"] = max((r["clasificacion"] for r in t["reglas"]),
                                 key=lambda c: RANGO.get(c, 0))
        t["nota"] = next((h["nota"] for h in t["hallazgos"] if h["nota"]), "")
        pseudo = [r for r in t["reglas"] if r.get("pseudocomentario")]
        if t["clasificacion"] == "pseudocomentario" and pseudo:
            t["pseudocomentario"] = pseudo[0]["pseudocomentario"]
            t["justificacion"] = pseudo[0].get("justificacion", "")
        t["instruccion"] = "\n\n".join(r.get("instruccion", "").strip() for r in t["reglas"])
        t["regla"] = " / ".join(r["nombre"] for r in t["reglas"])
        del t["reglas"]
    return lista


class Descargador:
    """Descarga el código bajo demanda; solo abre la conexión si hace falta."""

    def __init__(self, objetos: list[dict]):
        self.uris = {o["nombre"]: o["uri"] for o in objetos if o.get("uri")}
        self.conn = None

    def fuente(self, nombre: str) -> str:
        from sap.adt import adt_request
        from sap.repositorio import resolver
        from sap.rfc_client import get_connection

        if self.conn is None:
            self.conn = get_connection()
        uri = self.uris.get(nombre) or resolver(self.conn, "PROG", nombre)[1]
        r = adt_request(self.conn, "GET", f"{uri}/source/main", {"Accept": "text/plain"})
        if not r.ok:
            raise RuntimeError(f"{nombre}: {r.status} {r.reason}")
        return r.text

    def cerrar(self):
        if self.conn is not None:
            self.conn.close()


def fragmento(lineas: list[str], centro: int, radio: int) -> str:
    ini, fin = max(1, centro - radio), min(len(lineas), centro + radio)
    return "\n".join(f"{n:>5}{'>' if n == centro else ' '}| {lineas[n - 1]}"
                     for n in range(ini, fin + 1))


def escribir_tarea(carpeta: Path, programa: str, meta: dict, tareas: list[dict],
                   externas: list[dict], fuentes: dict, radio: int) -> None:
    md = [f"# Tarea ATC: {programa}", "",
          f"Revisión del {meta['fecha']} con la variante `{meta['variante']}`. "
          "Reglas de trabajo en `CLAUDE.md` (raíz del proyecto).", "",
          "| Tarea | Lugar de corrección | Clasificación | Hallazgos | Regla |",
          "|---|---|---|---|---|"]
    for t in tareas:
        ids = ", ".join(h["id"] for h in t["hallazgos"])
        md.append(f"| {t['id']} | {t['include']} línea {t['linea']} | {t['clasificacion']} | {ids} | {t['regla']} |")
    if any(t["clasificacion"] == "pseudocomentario" for t in tareas):
        md += ["", "Las tareas `pseudocomentario` las resuelve `aplicar.py`; no generes propuestas para ellas."]

    for t in tareas:
        md += ["", f"## {t['id']} · {t['include']} línea {t['linea']} · {t['clasificacion']}", "",
               "**Instrucción:**", "", t["instruccion"], "", "**Hallazgos:**", ""]
        for h in t["hallazgos"]:
            md.append(f"- **{h['id']}** [P{h['prioridad']}] {h['include']} línea {h['linea']}: "
                      f"{h['mensaje']} _({h['check']})_")
            # Datos de la base de simplificaciones (revisiones nuevas)
            extra = [f"objeto {h['ref_tipo']} {h['ref_objeto']}" if h.get("ref_objeto") else "",
                     f"categoría {h['categoria']}" if h.get("categoria") else "",
                     f"cambio {h['cambio']}" if h.get("cambio") else "",
                     f"nota: {h['nota_titulo']}" if h.get("nota_titulo") else "",
                     f"pseudocomentario SAP: `{h['pseudocomentario']}`" if h.get("pseudocomentario") else ""]
            if any(extra):
                md.append("  - " + " · ".join(e for e in extra if e))
        lineas = fuentes.get(t["include"])
        if lineas:
            md += ["", f"**Código en el lugar de corrección** (`original/{t['include']}.abap`):", "",
                   "```abap", fragmento(lineas, t["linea"], radio), "```"]
        for h in t["hallazgos"]:
            if (h["include"], h["linea"]) != (t["include"], t["linea"]) and fuentes.get(h["include"]):
                md += ["", f"Dónde se reporta {h['id']} (`{h['include']}` línea {h['linea']}):", "",
                       "```abap", fragmento(fuentes[h["include"]], h["linea"] or 1, 3), "```"]

    if externas:
        md += ["", "## Resuelto en otra carpeta", "",
               "Estos includes se corrigen en la carpeta de otro programa; no los toques aquí.", ""]
        for e in externas:
            md.append(f"- {e['include']} línea {e['linea']}: {e['mensaje']} → `trabajo/{e['carpeta']}`")

    (carpeta / "TAREA.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Prepara la carpeta de trabajo de un programa")
    ap.add_argument("programa")
    ap.add_argument("--revision", help="JSON de la Fase 1 (por defecto, el más reciente)")
    ap.add_argument("--forzar", action="store_true", help="Volver a descargar el código")
    args = ap.parse_args()

    programa = args.programa.upper()
    ruta_rev = Path(args.revision) if args.revision else ultima_revision(programa)
    revision = json.loads(ruta_rev.read_text(encoding="utf-8"))
    reglas = cargar_yaml("reglas_atc.yaml")["reglas"]
    preparacion = cargar_yaml("settings.yaml")["preparacion"]
    radio = preparacion["lineas_contexto"]
    # La prioridad llega de SAP como texto ("1"); en el YAML es número
    prioridades = {str(p) for p in preparacion.get("prioridades") or []}

    hallazgos = [h for h in revision["hallazgos"] if programa in h["origenes"]]
    for i, h in enumerate(sorted(hallazgos, key=lambda h: (h["include"], h["linea"] or 0)), 1):
        h["id"] = f"H{i:02d}"

    # Código de todos los includes involucrados (donde se reporta y donde se corrige)
    carpeta = TRABAJO / programa
    (carpeta / "original").mkdir(parents=True, exist_ok=True)
    necesarios = {h["include"] for h in hallazgos}
    necesarios |= {m.group(1).upper() for h in hallazgos if (m := REF.search(h["mensaje"]))}
    fuentes, desc = {}, Descargador(revision["objetos"])
    try:
        for inc in sorted(necesarios):
            archivo = carpeta / "original" / f"{inc.replace('/', '#')}.abap"
            if args.forzar or not archivo.exists():
                print(f"Descargando {inc}")
                # ADT devuelve CRLF; en modo texto Windows lo convertiría en CR CR LF
                archivo.write_text(desc.fuente(inc).replace("\r\n", "\n"), encoding="utf-8")
            fuentes[inc] = archivo.read_text(encoding="utf-8").splitlines()
    finally:
        desc.cerrar()

    # Includes que corrige otra carpeta (includes compartidos)
    TRABAJO.mkdir(exist_ok=True)
    registro = json.loads(REGISTRO.read_text(encoding="utf-8")) if REGISTRO.exists() else {}
    propias, externas = [], []
    for t in agrupar(hallazgos, reglas, fuentes, prioridades):
        duenio = registro.setdefault(t["include"], programa)
        if duenio == programa:
            propias.append(t)
        else:
            externas += [{**h, "carpeta": duenio} for h in t["hallazgos"]]
    REGISTRO.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")

    datos = {"programa": programa, "revision": ruta_rev.name, "meta": revision["meta"],
             "tareas": propias, "resuelto_en_otra_carpeta": externas}
    (carpeta / "hallazgos.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    escribir_tarea(carpeta, programa, revision["meta"], propias, externas, fuentes, radio)

    print(f"\n{programa}: {len(hallazgos)} hallazgos agrupados en {len(propias)} tareas"
          + (f" ({len(externas)} hallazgos resueltos en otra carpeta)" if externas else ""))
    print(f"Carpeta: {carpeta}")
    print(f'Siguiente paso, en Claude Code: "procesa trabajo\\{programa}"')


if __name__ == "__main__":
    main()

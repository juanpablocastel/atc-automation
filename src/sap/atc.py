"""Ejecución de ATC por los servicios ADT (lo mismo que hace Eclipse).

Flujo: variante -> worklist -> run -> leer hallazgos.
Las respuestas crudas se guardan en data/findings/ para poder revisarlas.
"""
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from sap.adt import adt_request, fetch_csrf

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "findings"


@dataclass
class Hallazgo:
    objeto: str          # objeto revisado (p. ej. el programa)
    tipo: str
    include: str         # dónde está realmente el hallazgo (puede ser un include)
    linea: int | None
    prioridad: str
    check_id: str
    check: str
    mensaje: str
    uri: str
    documentacion: str = ""      # enlace ADT a la documentación del hallazgo
    # Datos de la base de simplificaciones (columnas de SAP GUI), si el hallazgo los tiene
    ref_tipo: str = ""           # Ref. Object Type: TRAN, DOMA...
    ref_objeto: str = ""         # Referenced Object: CC33, MATNR...
    componente: str = ""         # Referenced Application Component
    categoria: str = ""          # Simplification Item Category: B, I...
    cambio: str = ""             # Change Category: T (Technical), F (Functional)
    nota_titulo: str = ""
    pseudocomentario: str = ""   # el que propone SAP para suprimir el hallazgo


def _local(tag: str) -> str:
    """Quita el namespace: '{http://...}finding' -> 'finding'."""
    return tag.rsplit("}", 1)[-1]


def _attrs(el) -> dict:
    return {_local(k): v for k, v in el.attrib.items()}


def _guardar(nombre: str, contenido: bytes) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / nombre).write_bytes(contenido)


def _check(r, paso: str) -> None:
    if not r.ok:
        raise RuntimeError(f"{paso}: {r.status} {r.reason}\n{r.text[:800]}")


def variante_por_defecto(conn) -> str:
    """Lee la variante ATC configurada en el sistema."""
    r = adt_request(conn, "GET", "/sap/bc/adt/atc/customizing",
                    {"Accept": "application/xml, application/vnd.sap.atc.customizing-v1+xml"})
    _check(r, "Customizing ATC")
    _guardar("customizing.xml", r.body)
    for el in ET.fromstring(r.body).iter():
        a = _attrs(el)
        if a.get("name") == "systemCheckVariant":
            return a.get("value", "")
    raise RuntimeError("No se encontró systemCheckVariant; revisa data/findings/customizing.xml")


def ejecutar_atc(conn, uri_objeto: str, variante: str, csrf: str | None = None) -> list[Hallazgo]:
    """Corre ATC sobre un objeto (por su URI ADT) y devuelve los hallazgos."""
    base = {"x-csrf-token": csrf} if csrf else {}

    # 1. Worklist
    r = adt_request(conn, "POST", f"/sap/bc/adt/atc/worklists?checkVariant={quote(variante)}",
                    {**base, "Accept": "text/plain"})
    _check(r, "Crear worklist")
    worklist = r.text.strip()

    # 2. Run
    cuerpo = f"""<?xml version="1.0" encoding="UTF-8"?>
<atc:run maximumVerdicts="1000" xmlns:atc="http://www.sap.com/adt/atc">
  <objectSets xmlns:adtcore="http://www.sap.com/adt/core">
    <objectSet kind="inclusive">
      <adtcore:objectReferences>
        <adtcore:objectReference adtcore:uri="{uri_objeto}"/>
      </adtcore:objectReferences>
    </objectSet>
  </objectSets>
</atc:run>""".encode("utf-8")
    r = adt_request(conn, "POST", f"/sap/bc/adt/atc/runs?worklistId={worklist}",
                    {**base, "Content-Type": "application/xml", "Accept": "application/xml"},
                    cuerpo)
    _check(r, "Ejecutar ATC")
    _guardar("run.xml", r.body)

    # 3. Hallazgos
    r = adt_request(conn, "GET",
                    f"/sap/bc/adt/atc/worklists/{worklist}?includeExemptedFindings=false",
                    {"Accept": "application/atc.worklist.v1+xml"})
    _check(r, "Leer worklist")
    _guardar("worklist.xml", r.body)
    hallazgos = _parsear(r.body)
    completar_detalle(conn, hallazgos)
    return hallazgos


# El mensaje nombra el objeto referenciado: "(TRAN CC33, see Note...)", "(DOMA MATNR, Note...)"
REF_EN_MENSAJE = re.compile(r"\([A-Z]{4} [^,)]+")
DETALLE = {
    "ref": re.compile(r"Referenced Object: (\S+) \((\w+)\)"),
    "componente": re.compile(r"Referenced Application Component: (\S+)"),
    "categoria": re.compile(r"Simplification Item Category: (\w+) \("),
    "cambio": re.compile(r"Change Category[^:]*: (\w+) \((\w+)\)"),
    "nota": re.compile(r"SAP Note Number: 0*\d+ \((.+?)(?: -)? Simplification Item Category"),
    "pseudo": re.compile(r'pseudo comment "(#EC [^"\s]+)'),
}


def _hasta_cierre(texto: str, inicio: int) -> str:
    """Texto desde inicio hasta el paréntesis que cierra el abierto justo antes (admite anidados)."""
    nivel = 1
    for i in range(inicio, len(texto)):
        nivel += {"(": 1, ")": -1}.get(texto[i], 0)
        if nivel == 0:
            return texto[inicio:i]
    return texto[inicio:]


def completar_detalle(conn, hallazgos: list[Hallazgo]) -> None:
    """Lee la documentación ADT de los hallazgos con objeto referenciado (simplificaciones,
    extensión de campos) y extrae las columnas que muestra SAP GUI. Un fallo no detiene nada."""
    for h in hallazgos:
        if not h.documentacion or not REF_EN_MENSAJE.search(h.mensaje):
            continue
        try:
            r = adt_request(conn, "GET", h.documentacion, {"Accept": "application/vnd.sap.adt.docu.v1+html"})
        except Exception:
            continue
        if not r.ok:
            continue
        texto = re.sub(r"<style.*?</style>|<script.*?</script>", " ", r.text, flags=re.S)
        texto = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto)))
        if m := DETALLE["ref"].search(texto):
            h.ref_objeto, h.ref_tipo = m.group(1), m.group(2)
        if m := DETALLE["componente"].search(texto):
            h.componente = m.group(1)
        if m := DETALLE["categoria"].search(texto):
            h.categoria = f"{m.group(1)} ({_hasta_cierre(texto, m.end())})"
        if m := DETALLE["cambio"].search(texto):
            h.cambio = f"{m.group(1)} ({m.group(2)})"
        if m := DETALLE["nota"].search(texto):
            h.nota_titulo = m.group(1).strip()
        if m := DETALLE["pseudo"].search(texto):
            h.pseudocomentario = '"' + m.group(1)


def _parsear(xml: bytes) -> list[Hallazgo]:
    hallazgos = []
    for obj in ET.fromstring(xml).iter():
        if _local(obj.tag) != "object":
            continue
        o = _attrs(obj)
        for f in obj.iter():
            if _local(f.tag) != "finding":
                continue
            a = _attrs(f)
            uri = a.get("location") or a.get("uri", "")
            linea = None
            if "#start=" in uri:
                try:
                    linea = int(uri.split("#start=")[1].split(",")[0])
                except ValueError:
                    pass
            doc = next((l.attrib.get("href", "") for l in f
                        if _local(l.tag) == "link" and l.attrib.get("rel", "").endswith("/documentation")), "")
            hallazgos.append(Hallazgo(
                objeto=o.get("name", ""), tipo=o.get("type", ""),
                include=_nombre_desde_uri(uri) or o.get("name", ""), linea=linea,
                prioridad=a.get("priority", ""), check_id=a.get("checkId", ""),
                check=a.get("checkTitle", ""), mensaje=a.get("messageTitle", ""), uri=uri,
                documentacion=doc,
            ))
    return hallazgos


def _nombre_desde_uri(uri: str) -> str:
    """'/sap/bc/adt/programs/includes/zx_f01/source/main#start=10,0' -> 'ZX_F01'."""
    ruta = uri.split("#")[0]
    if "/source/" not in ruta:
        return ""
    return unquote(ruta.split("/source/")[0].rstrip("/").split("/")[-1]).upper()


def uri_programa(nombre: str) -> str:
    return f"/sap/bc/adt/programs/programs/{quote(nombre.lower(), safe='')}"

"""Datos del repositorio vía ADT: tipo exacto de un objeto y su URI.

La búsqueda de ADT distingue, por ejemplo, un programa (PROG/P) de un
include (PROG/I) o una clase (CLAS/OC), y devuelve la URI correcta para ATC.
"""
import xml.etree.ElementTree as ET
from urllib.parse import quote

from sap.adt import adt_request

RUTA_POR_TIPO = {
    "PROG": "programs/programs",
    "CLAS": "oo/classes",
    "INTF": "oo/interfaces",
    "FUGR": "functions/groups",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def buscar(conn, nombre: str) -> list[dict]:
    uri = ("/sap/bc/adt/repository/informationsystem/search?operation=quickSearch"
           f"&query={quote(nombre.lower())}&maxResults=50")
    r = adt_request(conn, "GET", uri, {"Accept": "application/xml"})
    if not r.ok:
        return []
    return [{_local(k): v for k, v in el.attrib.items()}
            for el in ET.fromstring(r.body).iter() if _local(el.tag) == "objectReference"]


def resolver(conn, tipo: str, nombre: str) -> tuple[str, str]:
    """Devuelve (tipo ADT, URI). Ej.: ('PROG/I', '/sap/bc/adt/programs/includes/zx_frm')."""
    for a in buscar(conn, nombre):
        if a.get("name", "").upper() == nombre.upper() and a.get("type", "").split("/")[0] == tipo:
            return a["type"], a["uri"]
    return tipo, f"/sap/bc/adt/{RUTA_POR_TIPO[tipo]}/{quote(nombre.lower(), safe='')}"

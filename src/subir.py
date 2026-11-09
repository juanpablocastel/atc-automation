"""
Fase 4 - Sube a SAP el código de corregido\\ (por el túnel RFC).

Por cada include de trabajo\\<PROGRAMA>\\corregido\\:
  1. Compara el código actual en SAP con original\\; si cambió, no lo toca.
  2. Bloquea el objeto, escribe el código nuevo y lo desbloquea.
  3. Ejecuta el syntax check sobre la versión inactiva.
Por defecto queda INACTIVO. Con --activar, activa todos los objetos escritos juntos,
solo si se escribieron todos y el syntax check no dio errores.

Sin --escribir solo simula (no modifica nada). Para escribir, escritura.modo
en config\\settings.yaml debe ser "rfc".

Uso:
    python src\\subir.py ZBT_CONTROLKIT                          (simulación)
    python src\\subir.py ZBT_CONTROLKIT --escribir --orden C12K900123
    python src\\subir.py ZBT_CONTROLKIT --escribir --orden C12K900123 --activar
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import yaml

from sap.adt import adt_request, fetch_csrf
from sap.repositorio import resolver
from sap.rfc_client import get_connection

RAIZ = Path(__file__).resolve().parents[1]
LOCK_ACCEPT = "application/*,application/vnd.sap.as+xml;charset=UTF-8;dataname=com.sap.adt.lock.result"


def normalizar(texto: str) -> list[str]:
    return [l.rstrip() for l in texto.replace("\r\n", "\n").rstrip("\n").split("\n")]


def bloquear(conn, uri: str, base: dict) -> tuple[str, str]:
    r = adt_request(conn, "POST", f"{uri}?_action=LOCK&accessMode=MODIFY", {**base, "Accept": LOCK_ACCEPT})
    if not r.ok:
        raise RuntimeError(f"no se pudo bloquear: {r.status} {r.reason} {r.text[:300]}")
    handle = re.search(r"<LOCK_HANDLE>(.*?)</LOCK_HANDLE>", r.text)
    corrnr = re.search(r"<CORRNR>(.*?)</CORRNR>", r.text)
    if not handle:
        raise RuntimeError(f"respuesta de bloqueo sin LOCK_HANDLE: {r.text[:300]}")
    return handle.group(1), corrnr.group(1) if corrnr else ""


def desbloquear(conn, uri: str, handle: str, base: dict) -> None:
    adt_request(conn, "POST", f"{uri}?_action=UNLOCK&lockHandle={quote(handle, safe='')}", base)


def syntax_check(conn, uri: str, base: dict) -> list[str]:
    cuerpo = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<chkrun:checkObjectList xmlns:chkrun="http://www.sap.com/adt/checkrun" '
              'xmlns:adtcore="http://www.sap.com/adt/core">'
              f'<chkrun:checkObject adtcore:uri="{uri}" chkrun:version="inactive"/>'
              '</chkrun:checkObjectList>').encode("utf-8")
    r = adt_request(conn, "POST", "/sap/bc/adt/checkruns?reporters=abapCheckRun",
                    {**base, "Content-Type": "application/vnd.sap.adt.checkobjects+xml",
                     "Accept": "application/vnd.sap.adt.checkmessages+xml"}, cuerpo)
    if not r.ok:
        return [f"no se pudo ejecutar: {r.status} {r.reason}"]
    mensajes = []
    for el in ET.fromstring(r.body).iter():
        if el.tag.rsplit("}", 1)[-1] == "checkMessage":
            attrs = {k.rsplit("}", 1)[-1]: v for k, v in el.attrib.items()}
            # uri del mensaje: /sap/bc/adt/programs/includes/<include>/source/main#start=<línea>,<col>
            lugar = re.search(r"/([^/]+)/source/main#start=(\d+)", attrs.get("uri", ""))
            donde = f"{lugar.group(1).upper()} línea {lugar.group(2)}" if lugar else "?"
            mensajes.append(f"[{attrs.get('type', '?')}] {donde}: {attrs.get('shortText', '')}")
    return mensajes


def activar(conn, objetos: list[tuple[str, str]], base: dict) -> tuple[bool, list[str]]:
    """Activa los objetos juntos (como Eclipse). Devuelve (activado, mensajes)."""
    refs = "".join(f'<adtcore:objectReference adtcore:uri="{uri}" adtcore:name="{nombre}"/>'
                   for nombre, uri in objetos)
    cuerpo = ('<?xml version="1.0" encoding="UTF-8"?>'
              f'<adtcore:objectReferences xmlns:adtcore="http://www.sap.com/adt/core">{refs}'
              '</adtcore:objectReferences>').encode("utf-8")
    r = adt_request(conn, "POST", "/sap/bc/adt/activation?method=activate&preauditRequested=true",
                    {**base, "Content-Type": "application/xml", "Accept": "application/xml"}, cuerpo)
    if not r.ok:
        return False, [f"no se pudo activar: {r.status} {r.reason} {r.text[:300]}"]
    if not r.body.strip():
        return True, []
    raiz = ET.fromstring(r.body)
    local = lambda el: el.tag.rsplit("}", 1)[-1]
    if local(raiz) == "inactiveObjects":
        # SAP pide activar más objetos a la vez: no se activa nada por nuestra cuenta
        nombres = {v for el in raiz.iter() for k, v in el.attrib.items() if k.endswith("name")}
        return False, [f"SAP pide activar también otros objetos inactivos: {', '.join(sorted(nombres))}"]
    mensajes, error = [], False
    for el in raiz.iter():
        if local(el) == "msg":
            tipo = el.attrib.get("type", "?")
            texto = " ".join(t.text or "" for t in el.iter() if local(t) == "txt").strip()
            error |= tipo in ("E", "A")
            mensajes.append(f"[{tipo}] {el.attrib.get('objDescr', '')}: {texto}")
    return not error, mensajes


def main():
    ap = argparse.ArgumentParser(description="Sube corregido\\ a SAP (inactivo, o activado con --activar)")
    ap.add_argument("programa")
    ap.add_argument("--escribir", action="store_true", help="Escribir en SAP (sin esto, solo simula)")
    ap.add_argument("--orden", help="Orden de transporte (si el objeto no está ya en una)")
    ap.add_argument("--activar", action="store_true",
                    help="Activar después de escribir (solo si todo se escribió y no hay errores de sintaxis)")
    args = ap.parse_args()

    carpeta = RAIZ / "trabajo" / args.programa.upper()
    archivos = sorted((carpeta / "corregido").glob("*.abap"))
    if not archivos:
        sys.exit(f"No hay código en {carpeta / 'corregido'}. Ejecuta aplicar.py primero.")
    modo = yaml.safe_load((RAIZ / "config" / "settings.yaml").read_text(encoding="utf-8"))["escritura"]["modo"]
    if args.escribir and modo != "rfc":
        sys.exit(f'escritura.modo es "{modo}"; para escribir con este script debe ser "rfc".')

    print("ESCRITURA EN SAP (versión inactiva)" if args.escribir else "SIMULACIÓN: no se modifica nada")
    conn = get_connection()
    resumen, checks, escritos, activacion = [], {}, [], None
    try:
        base = {"X-sap-adt-sessiontype": "stateful"}
        if args.escribir:
            base["x-csrf-token"] = fetch_csrf(conn) or ""
        for archivo in archivos:
            inc = archivo.stem.replace("#", "/")
            original = carpeta / "original" / archivo.name
            _, uri = resolver(conn, "PROG", inc)
            print(f"\n{inc}  ({uri})")

            r = adt_request(conn, "GET", f"{uri}/source/main", {"Accept": "text/plain"})
            if not r.ok:
                resumen.append((inc, f"error al leer: {r.status} {r.reason}"))
                continue
            if normalizar(r.text) != normalizar(original.read_text(encoding="utf-8")):
                resumen.append((inc, "NO SUBIDO: el código en SAP cambió desde la descarga"))
                continue
            nuevo = archivo.read_text(encoding="utf-8")
            if not args.escribir:
                resumen.append((inc, f"se subiría ({len(normalizar(nuevo))} líneas); SAP coincide con original"))
                continue

            handle, corrnr = bloquear(conn, uri, base)
            try:
                orden = args.orden or corrnr
                params = f"lockHandle={quote(handle, safe='')}" + (f"&corrNr={orden}" if orden else "")
                r = adt_request(conn, "PUT", f"{uri}/source/main?{params}",
                                {**base, "Content-Type": "text/plain; charset=utf-8"}, nuevo.encode("utf-8"))
            finally:
                desbloquear(conn, uri, handle, base)
            if not r.ok:
                resumen.append((inc, f"ERROR al escribir: {r.status} {r.reason} {r.text[:300]}"))
                continue
            mensajes = syntax_check(conn, uri, base)
            checks[inc] = mensajes
            for m in mensajes:
                print(f"   {m}")
            check = f"{len(mensajes)} mensajes" if mensajes else "sin mensajes"
            resumen.append((inc, f"escrito (inactivo), orden {orden or '-'}; syntax check {check}"))
            escritos.append((inc, uri))

        if args.escribir and args.activar:
            errores_sintaxis = [m for ms in checks.values() for m in ms if m.startswith("[E]")]
            if len(escritos) != len(archivos):
                activacion = {"activado": False, "mensajes": ["No se activa: no se escribieron todos los includes."]}
            elif errores_sintaxis:
                activacion = {"activado": False, "mensajes": ["No se activa: el syntax check dio errores."]}
            else:
                print("\nActivando: " + ", ".join(n for n, _ in escritos))
                ok, mensajes = activar(conn, escritos, base)
                activacion = {"activado": ok, "mensajes": mensajes}
    finally:
        conn.close()

    print("\nResumen:")
    for inc, estado in resumen:
        print(f"  {inc}: {estado}")
    if args.escribir:
        # Registro para la interfaz: qué se subió, cuándo y con qué orden
        registro = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "orden": args.orden or "",
                    "includes": [{"include": inc, "subido": estado.startswith("escrito"), "estado": estado,
                                  "syntax_check": checks.get(inc, [])} for inc, estado in resumen],
                    "activacion": activacion}
        (carpeta / "subida.json").write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
        if activacion is None:
            print("\nNada se activó. Revisa en Eclipse y activa ahí.")
        else:
            print("\nACTIVADO" if activacion["activado"] else "\nNO SE ACTIVÓ (queda inactivo)")
            for m in activacion["mensajes"]:
                print(f"   {m}")
        if activacion and not activacion["activado"]:
            sys.exit(1)


if __name__ == "__main__":
    main()

"""
Documentación de entrega: entradas de remediación de uno o varios programas en el
formato del consolidado del cliente, como HTML para copiar y pegar en Word.

Una entrada por nota SAP e include:
  <nota> – PROG – <include> – líneas <l1 l2 ...>      (Arial Bold 10, naranja)
  Solución                                            (Arial 10, negrita, subrayado)
  <justificación>                                     (Arial 10)
  <código con las marcas, como queda en corregido\\>   (Arial 7)

La interfaz la muestra en pantalla con un botón para copiar. Por consola deja un .html.

Uso:
    python src\\documentar.py ZPROG1 ZPROG2
"""
import argparse
import html
import json
from datetime import datetime
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
TRABAJO = RAIZ / "trabajo"
SALIDA = RAIZ / "output" / "documentacion"
NARANJA = "#C55A11"
LINEAS_CONTEXTO = 2     # líneas de código antes del bloque marcado


def leer_json(ruta: Path, defecto=None):
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else defecto


def marcas() -> dict:
    return yaml.safe_load((RAIZ / "config" / "settings.yaml").read_text(encoding="utf-8"))["marcas"]


def bloque_marcado(carpeta: Path, c: dict, m: dict) -> list[str]:
    """El bloque del cambio tal como quedó en corregido\\ (marcas incluidas) con contexto previo."""
    datos = {"nota": c.get("nota") or m.get("nota_por_defecto") or "", "linea": c["linea_ini"]}
    inicio = m["orig_inicio" if c["tipo"] == "reemplazar" else "rem_inicio"].format(**datos)
    fin = m["rem_fin"].format(**datos)
    archivo = carpeta / "corregido" / f"{c['include'].replace('/', '#')}.abap"
    lineas = archivo.read_text(encoding="utf-8").splitlines() if archivo.exists() else []
    if inicio in lineas:
        i = lineas.index(inicio)
        j = lineas.index(fin, i) if fin in lineas[i:] else i
        desde = max(0, i - LINEAS_CONTEXTO)
        return [l for l in lineas[desde:i] if l.strip()] + lineas[i:j + 1]
    # Sin corregido\ (o distinto): se reconstruye igual que aplicar.py
    orig = [inicio, *[m["prefijo_comentario"] + l for l in c.get("original", [])],
            m["orig_fin"].format(**datos)] if c["tipo"] == "reemplazar" else []
    return orig + [m["rem_inicio"].format(**datos), *c["codigo_nuevo"], fin]


def entradas(programa: str) -> list[dict]:
    """Entradas del programa agrupadas por nota e include."""
    carpeta = TRABAJO / programa
    m = marcas()
    grupos = {}
    for c in sorted(leer_json(carpeta / "cambios.json", {}).get("cambios", []),
                    key=lambda c: (c["include"], c["linea_ini"])):
        g = grupos.setdefault((c.get("nota") or "", c["include"]), {"cambios": [], "textos": []})
        g["cambios"].append(c)
        if c.get("justificacion") and c["justificacion"] not in g["textos"]:
            g["textos"].append(c["justificacion"])
    return [{"programa": programa, "nota": nota, "include": inc, **g,
             "codigo": [bloque_marcado(carpeta, c, m) for c in g["cambios"]]}
            for (nota, inc), g in grupos.items()]


def a_html(lista: list[dict]) -> str:
    """Entradas con estilos en línea (fuente, tamaño, color): Word los conserva al pegar."""
    esc = html.escape
    base = "font-family:Arial;margin:0;color:#000"
    partes = []
    for e in lista:
        lineas = " ".join(str(c["linea_ini"]) for c in e["cambios"])
        partes.append(f'<p style="{base};font-size:10pt;font-weight:bold;color:{NARANJA};margin-top:14pt">'
                      f'{esc(e["nota"] or "sin nota")} – PROG – {esc(e["include"])} – líneas {lineas}</p>')
        partes.append(f'<p style="{base};font-size:10pt;font-weight:bold;text-decoration:underline;'
                      f'margin-top:10pt">Solución</p>')
        partes += [f'<p style="{base};font-size:10pt;margin-top:8pt">{esc(t)}</p>' for t in e["textos"]]
        for bloque in e["codigo"]:
            partes.append(f'<div style="margin-top:8pt">' + "".join(
                f'<p style="{base};font-size:7pt;white-space:pre">{esc(l) or "&nbsp;"}</p>' for l in bloque)
                + "</div>")
    return "\n".join(partes)


def main():
    ap = argparse.ArgumentParser(description="Documentación de remediación para copiar y pegar")
    ap.add_argument("programas", nargs="+")
    args = ap.parse_args()
    lista = [e for p in args.programas for e in entradas(p.upper())]
    if not lista:
        print("No hay cambios aplicados para documentar.")
        return
    SALIDA.mkdir(parents=True, exist_ok=True)
    archivo = SALIDA / f"remediacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    archivo.write_text(f'<html><meta charset="utf-8"><body>{a_html(lista)}</body></html>', encoding="utf-8")
    print(f"{len(lista)} entradas en {archivo} (ábrelo, Ctrl+A, Ctrl+C y pega en Word)")


if __name__ == "__main__":
    main()

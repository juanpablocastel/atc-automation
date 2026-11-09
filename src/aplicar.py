"""
Fase 2 - Aplica las propuestas con las marcas del cliente.

Lee trabajo\\<PROGRAMA>\\propuestas.json (lo escribe Claude), agrega las
correcciones estándar con pseudocomentario y genera:
  corregido\\<INCLUDE>.abap  -> código con las marcas de remediación
  cambios.json              -> detalle de cada cambio, para revisión y para el Word
No modifica nada en SAP.

Uso:
    python src\\aplicar.py ZBT_CONTROLKIT
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]


def sin_comentario(linea: str) -> str:
    """Código de la línea sin comentarios (respeta literales '...', `...` y |...|)."""
    if linea.startswith("*"):
        return ""
    salida, dentro = [], None
    for ch in linea:
        if dentro:
            salida.append(ch)
            if ch == dentro:
                dentro = None
        elif ch in "'`|":
            dentro = ch
            salida.append(ch)
        elif ch == '"':
            break
        else:
            salida.append(ch)
    return "".join(salida).rstrip()


def fin_de_sentencia(lineas: list[str], ini: int) -> int | None:
    for n in range(ini, min(len(lineas), ini + 300) + 1):
        if sin_comentario(lineas[n - 1]).endswith("."):
            return n
    return None


def automaticas(tareas: list[dict], fuentes: dict, errores: list[str]) -> list[dict]:
    """Propuestas con pseudocomentario: se agrega al final de la sentencia."""
    res = []
    for t in tareas:
        if t["clasificacion"] != "pseudocomentario":
            continue
        lineas = fuentes.get(t["include"])
        fin = fin_de_sentencia(lineas, t["linea"]) if lineas else None
        if not t.get("pseudocomentario") or not fin:
            errores.append(f"{t['id']}: no se pudo ubicar la sentencia para el pseudocomentario")
            continue
        ultima = lineas[fin - 1]
        if sin_comentario(ultima) != ultima.rstrip():
            errores.append(f"{t['id']}: la línea {fin} ya tiene un comentario; resolver a mano")
            continue
        nuevo = lineas[t["linea"] - 1:fin]
        nuevo[-1] = ultima.rstrip() + " " + t["pseudocomentario"]
        res.append({"id": f"A{len(res) + 1:02d}", "tarea": t["id"], "include": t["include"],
                    "tipo": "reemplazar", "linea_ini": t["linea"], "linea_fin": fin,
                    "codigo_nuevo": nuevo, "resuelve": [h["id"] for h in t["hallazgos"]],
                    "justificacion": t.get("justificacion") or
                    f"Caso estándar: se agrega el pseudocomentario {t['pseudocomentario']}.",
                    "confianza": "alta", "verificar": "", "origen": "automatico"})
    return res


def validar(props: list[dict], fuentes: dict, tareas: dict, errores: list[str]) -> None:
    por_include = {}
    for p in props:
        pid = p.get("id", "?")
        faltan = [c for c in ("tarea", "include", "tipo", "linea_ini", "linea_fin", "codigo_nuevo")
                  if c not in p]
        if faltan:
            errores.append(f"{pid}: faltan campos {faltan}")
            continue
        if p["include"] not in fuentes:
            errores.append(f"{pid}: no existe original/{p['include']}.abap")
            continue
        if p["tarea"] not in tareas:
            errores.append(f"{pid}: la tarea {p['tarea']} no existe")
        n = len(fuentes[p["include"]])
        if p["tipo"] not in ("reemplazar", "insertar"):
            errores.append(f"{pid}: tipo '{p['tipo']}' no válido")
        elif not (1 <= p["linea_ini"] <= p["linea_fin"] <= n):
            errores.append(f"{pid}: rango {p['linea_ini']}-{p['linea_fin']} fuera del archivo (1-{n})")
        elif p["tipo"] == "insertar" and p["linea_ini"] != p["linea_fin"]:
            errores.append(f"{pid}: en 'insertar', linea_fin debe ser igual a linea_ini")
        por_include.setdefault(p["include"], []).append(p)
    for inc, lista in por_include.items():
        lista.sort(key=lambda p: (p["linea_ini"], p["linea_fin"]))
        for a, b in zip(lista, lista[1:]):
            if b["linea_ini"] <= a["linea_fin"]:
                errores.append(f"{a['id']} y {b['id']} se superponen en {inc}")


def marcar(lineas: list[str], props: list[dict], marcas: dict, nota_tarea: dict) -> list[str]:
    salida = list(lineas)
    for p in sorted(props, key=lambda p: p["linea_ini"], reverse=True):
        ini, fin = p["linea_ini"], p["linea_fin"]
        datos = {"nota": nota_tarea.get(p["tarea"]) or marcas.get("nota_por_defecto") or "", "linea": ini}
        rem = [marcas["rem_inicio"].format(**datos), *p["codigo_nuevo"], marcas["rem_fin"].format(**datos)]
        if p["tipo"] == "insertar":
            salida[ini:ini] = rem
        else:
            orig = [marcas["orig_inicio"].format(**datos),
                    *[marcas["prefijo_comentario"] + l for l in lineas[ini - 1:fin]],
                    marcas["orig_fin"].format(**datos)]
            salida[ini - 1:fin] = orig + rem
    return salida


def main():
    ap = argparse.ArgumentParser(description="Aplica las propuestas con las marcas del cliente")
    ap.add_argument("programa")
    args = ap.parse_args()

    carpeta = RAIZ / "trabajo" / args.programa.upper()
    marcas = yaml.safe_load((RAIZ / "config" / "settings.yaml").read_text(encoding="utf-8"))["marcas"]
    datos = json.loads((carpeta / "hallazgos.json").read_text(encoding="utf-8"))
    tareas = {t["id"]: t for t in datos["tareas"]}
    fuentes = {f.stem.replace("#", "/"): f.read_text(encoding="utf-8").splitlines()
               for f in (carpeta / "original").glob("*.abap")}

    archivo_prop = carpeta / "propuestas.json"
    prop = json.loads(archivo_prop.read_text(encoding="utf-8")) if archivo_prop.exists() else {}
    errores, avisos = [], []
    props = [{**p, "origen": "claude"} for p in prop.get("propuestas", [])]
    props += automaticas(datos["tareas"], fuentes, errores)
    validar(props, fuentes, tareas, errores)

    # Un include que se corrige aquí queda registrado a nombre de este programa,
    # aunque no tenga hallazgos propios (por ejemplo, el TOP con una declaración)
    archivo_reg = RAIZ / "trabajo" / "_includes.json"
    registro = json.loads(archivo_reg.read_text(encoding="utf-8")) if archivo_reg.exists() else {}
    for inc in sorted({p["include"] for p in props}):
        duenio = registro.setdefault(inc, datos["programa"])
        if duenio != datos["programa"]:
            errores.append(f"{inc} lo corrige la carpeta trabajo\\{duenio}; no se puede cambiar aquí")

    # Tareas que esperan respuesta y no la tienen
    atendidas = {p["tarea"] for p in props} | {s["tarea"] for s in prop.get("sin_cambio", [])}
    for t in datos["tareas"]:
        if t["clasificacion"] in ("propuesta", "automatico") and t["id"] not in atendidas:
            avisos.append(f"{t['id']}: sin propuesta ni motivo en 'sin_cambio'")
    nota_tarea = {tid: t.get("nota", "") for tid, t in tareas.items()}
    for p in props:
        if not (nota_tarea.get(p["tarea"]) or marcas.get("nota_por_defecto")):
            avisos.append(f"{p['id']}: sin número de nota para las marcas")

    if errores:
        print("ERRORES (no se generó nada):")
        for e in errores:
            print(f"  - {e}")
        sys.exit(1)

    archivo_reg.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    destino = carpeta / "corregido"
    destino.mkdir(exist_ok=True)
    for viejo in destino.glob("*.abap"):
        viejo.unlink()
    por_include = {}
    for p in props:
        por_include.setdefault(p["include"], []).append(p)
    for inc, lista in sorted(por_include.items()):
        nuevo = marcar(fuentes[inc], lista, marcas, nota_tarea)
        (destino / f"{inc.replace('/', '#')}.abap").write_text("\n".join(nuevo) + "\n", encoding="utf-8")
        print(f"{inc}: {len(lista)} cambios")

    hallazgos = {h["id"]: h for t in datos["tareas"] for h in t["hallazgos"]}
    cambios = [{**p, "nota": nota_tarea.get(p["tarea"], ""),
                "original": fuentes[p["include"]][p["linea_ini"] - 1:p["linea_fin"]] if p["tipo"] == "reemplazar" else [],
                "hallazgos": [hallazgos[h] for h in p.get("resuelve", []) if h in hallazgos]}
               for p in sorted(props, key=lambda p: (p["include"], p["linea_ini"]))]
    (carpeta / "cambios.json").write_text(json.dumps(
        {"programa": datos["programa"], "cambios": cambios, "sin_cambio": prop.get("sin_cambio", [])},
        ensure_ascii=False, indent=2), encoding="utf-8")

    for a in avisos:
        print(f"AVISO: {a}")
    print(f"\nCódigo con marcas en: {destino}")


if __name__ == "__main__":
    main()

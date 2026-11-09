"""
Interfaz local del flujo ATC: programas, hallazgos, estado, comparación de código
y ejecución de cada fase (una a una o encadenadas en un flujo automático).

Uso:
    python -m streamlit run src\\interfaz.py
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import yaml

RAIZ = Path(__file__).resolve().parents[1]
TRABAJO = RAIZ / "trabajo"
REVISIONES = RAIZ / "data" / "revisiones"
sys.path.insert(0, str(RAIZ / "src"))

ICONO = {"Pendiente": "🔴", "Propuesto": "🟡", "Propuesto (falta aplicar)": "🟠", "Aplicado": "🔵",
         "Subido (inactivo)": "🟢", "Sin cambio": "⚪", "Informativo": "⚪"}
PASOS = {"revision": "Revisión ATC", "preparar": "Armar tareas", "propuestas": "Propuestas (Claude + aplicar)",
         "aplicar": "Aplicar", "subir": "Subir a SAP"}
# Estilos limitados a .diffatc: la tabla se inserta en la página (st.html), no en un iframe
ESTILO_DIFF = """<style>
  .diffatc{background:#fff;color:#000;font-family:Consolas,monospace;font-size:12px;overflow:auto}
  .diffatc table.diff{border-collapse:collapse;width:100%;margin:0}
  .diffatc .diff_header{background:#f0f0f0;color:#888;padding:0 6px;text-align:right}
  .diffatc td{padding:0 6px;white-space:pre-wrap;vertical-align:top;border:none}
  .diffatc th{background:#f0f0f0;color:#555;padding:2px 6px;font-family:sans-serif;border:none}
  .diffatc .diff_next{display:none}
  .diffatc .diff_add{background:#d7f5dd}.diffatc .diff_chg{background:#fff3c4}.diffatc .diff_sub{background:#fbd5d5}
</style>"""


# ---------------------------------------------------------------- datos

def leer_json(ruta: Path, defecto=None):
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else defecto


def mtime(ruta: Path) -> float:
    return ruta.stat().st_mtime if ruta.exists() else 0


def settings() -> dict:
    return yaml.safe_load((RAIZ / "config" / "settings.yaml").read_text(encoding="utf-8"))


def revisiones() -> list[dict]:
    """Revisiones de Fase 1, de la más reciente a la más antigua."""
    res = []
    for ruta in sorted(REVISIONES.glob("revision_*.json"), reverse=True):
        datos = leer_json(ruta)
        res.append({"archivo": ruta.name, "meta": datos["meta"], "hallazgos": datos["hallazgos"],
                    "objetos": datos.get("objetos", [])})
    return res


def programas() -> list[str]:
    """Programas en orden de llegada: la primera revisión ATC que los incluyó."""
    llegada = {}
    for r in sorted(revisiones(), key=lambda r: r["archivo"]):
        for p in r["meta"]["programas"]:
            llegada.setdefault(p, r["archivo"])
    for d in TRABAJO.glob("*"):
        if d.is_dir() and not d.name.startswith("_"):
            # Carpeta sin revisión: cuenta la fecha de creación, con el mismo formato que los archivos
            llegada.setdefault(d.name, datetime.fromtimestamp(d.stat().st_ctime).strftime("revision_%Y%m%d_%H%M"))
    return sorted(llegada, key=lambda p: (llegada[p], p))


RESUELTOS = {"Subido (inactivo)", "Sin cambio"}


def pendientes(info: dict, programa: str) -> tuple[int, int]:
    """(pendientes, total) de los hallazgos en alcance. Con una verificación ATC posterior
    manda lo que dice ATC; si no, cuenta como resuelto lo subido o lo documentado sin cambio
    (un informativo sigue pendiente hasta que se documenta en sin_cambio)."""
    alcance = [h for h in hallazgos_revision(info, programa) if en_alcance(h)]
    verif = verificar(info, programa)
    if verif:
        return sum(verif[0].get(clave(h), True) for h in alcance), len(alcance)
    estado = {clave(h): t["estado"] for t in info["tareas"] for h in t["hallazgos"]}
    return sum(estado.get(clave(h)) not in RESUELTOS for h in alcance), len(alcance)


def cargar(programa: str) -> dict:
    """Todo lo que se sabe de un programa y el estado de cada tarea."""
    carpeta = TRABAJO / programa
    datos = leer_json(carpeta / "hallazgos.json")
    revs = [r for r in revisiones() if programa in r["meta"]["programas"]]
    ultima = revs[0] if revs else None
    # La revisión con la que se preparó el programa; una posterior sirve para verificar
    base = next((r for r in revs if datos and r["archivo"] == datos.get("revision")), ultima)
    prop = leer_json(carpeta / "propuestas.json", {})
    cambios = leer_json(carpeta / "cambios.json", {})
    subida = leer_json(carpeta / "subida.json", {})
    info = {"carpeta": carpeta, "datos": datos, "revision": base, "propuestas": prop,
            "cambios": cambios, "subida": subida, "tareas": [],
            "verificacion": ultima if ultima and base and ultima["archivo"] > base["archivo"] else None,
            # Datos de simplificación por firma, de cualquier revisión (las antiguas no los traen)
            "ref": {firma(h): h for r in reversed(revs) for h in r["hallazgos"] if h.get("ref_objeto")}}
    if not datos:
        return info

    aplicado_al_dia = mtime(carpeta / "cambios.json") >= mtime(carpeta / "propuestas.json")
    subido_al_dia = mtime(carpeta / "subida.json") >= mtime(carpeta / "cambios.json")
    subidos = {i["include"] for i in subida.get("includes", []) if i.get("subido")}
    con_propuesta = {p["tarea"] for p in prop.get("propuestas", [])}
    sin_cambio = {s["tarea"]: s["motivo"] for s in prop.get("sin_cambio", [])}
    cambios_por_tarea = {}
    for c in cambios.get("cambios", []):
        cambios_por_tarea.setdefault(c["tarea"], []).append(c)

    for t in datos["tareas"]:
        cs = cambios_por_tarea.get(t["id"], [])
        if t["id"] in sin_cambio and not cs:
            estado = "Sin cambio"
        elif t["clasificacion"] == "reporte" and t["id"] not in con_propuesta:
            estado = "Informativo"
        elif cs and aplicado_al_dia:
            estado = ("Subido (inactivo)" if subido_al_dia and all(c["include"] in subidos for c in cs)
                      else "Aplicado")
        elif t["id"] in con_propuesta:
            estado = "Propuesto (falta aplicar)" if cambios else "Propuesto"
        else:
            estado = "Pendiente"
        info["tareas"].append({**t, "estado": estado, "motivo": sin_cambio.get(t["id"], "")})
    return info


def etapas(info: dict) -> list[tuple[str, bool]]:
    c = info["carpeta"]
    return [("Revisado", info["revision"] is not None), ("Preparado", info["datos"] is not None),
            ("Propuestas", (c / "propuestas.json").exists()), ("Aplicado", (c / "cambios.json").exists()),
            ("Subido", (c / "subida.json").exists()), ("Verificado", info.get("verificacion") is not None)]


def prioridades() -> set[str]:
    """Prioridades configuradas en settings.yaml (vacío = todas)."""
    return {str(p) for p in settings().get("preparacion", {}).get("prioridades") or []}


def en_alcance(h: dict) -> bool:
    return not prioridades() or str(h["prioridad"]) in prioridades()


def hallazgos_revision(info: dict, programa: str) -> list[dict]:
    """Todos los hallazgos de la revisión más reciente del programa (sin filtrar)."""
    return [h for h in info["revision"]["hallazgos"] if programa in h["origenes"]] if info["revision"] else []


def clave(h: dict) -> tuple:
    return h["include"], h["linea"], h["check_id"], h["mensaje"]


def firma(h: dict) -> tuple:
    """Identidad de un hallazgo sin números de línea (cambian al corregir el código)."""
    return h["include"], h["check_id"], re.sub(r"\d+", "#", h["mensaje"])


def verificar(info: dict, programa: str) -> tuple[dict, list[dict]] | None:
    """Compara la revisión base con una posterior: {clave: sigue?} y los hallazgos nuevos."""
    if not info["verificacion"]:
        return None
    pendientes = Counter(firma(h) for h in info["verificacion"]["hallazgos"] if programa in h["origenes"])
    sigue = {}
    for h in hallazgos_revision(info, programa):
        sigue[clave(h)] = pendientes[firma(h)] > 0
        if sigue[clave(h)]:
            pendientes[firma(h)] -= 1
    nuevos = []
    for h in info["verificacion"]["hallazgos"]:
        if programa in h["origenes"] and pendientes[firma(h)] > 0:
            pendientes[firma(h)] -= 1
            nuevos.append(h)
    return sigue, nuevos


# ---------------------------------------------------------------- ejecución

def entorno() -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # "python" en los subprocesos (incluido Claude) debe ser el del .venv
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def correr(comando: list[str], timeout: int = 1800) -> tuple[bool, str]:
    try:
        r = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL, env=entorno(), timeout=timeout)
        salida = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
        return r.returncode == 0, salida or "(sin salida)"
    except subprocess.TimeoutExpired:
        return False, f"Se cortó después de {timeout // 60} minutos."
    except FileNotFoundError as e:
        return False, f"No se encontró el comando: {e}"


def guardar_resultado(titulo: str, ok: bool, salida: str, resumen: list[str] | None = None) -> None:
    st.session_state["ultima"] = {"titulo": titulo, "ok": ok, "salida": salida, "resumen": resumen or [],
                                  "hora": datetime.now().strftime("%H:%M:%S")}
    st.rerun()


def resumen_revision(programa: str) -> str:
    rev = next((r for r in revisiones() if programa in r["meta"]["programas"]), None)
    todos = [h for h in rev["hallazgos"] if programa in h["origenes"]] if rev else []
    pr = "/".join(f"P{p}" for p in sorted(prioridades())) or "todas"
    return f"{programa}: {len(todos)} hallazgos, {sum(map(en_alcance, todos))} en alcance ({pr})"


def script(nombre: str, *args: str) -> list[str]:
    return [sys.executable, str(RAIZ / "src" / nombre), *args]


def comando_claude(programa: str) -> list[str] | None:
    comando = [a.replace("{programa}", programa) for a in settings().get("interfaz", {}).get("claude", [])]
    if not comando:
        return None
    exe = shutil.which(comando[0])
    nativo = Path.home() / ".local" / "bin" / "claude.exe"   # ubicación del instalador nativo
    if not exe and comando[0] == "claude" and nativo.exists():
        exe = str(nativo)
    return [exe, *comando[1:]] if exe else None


def comando_paso(paso: str, programa: str, opciones: dict) -> list[str] | None:
    cfg = settings()["atc"]
    if paso == "revision":
        return script("fase1_revision.py", programa, "--variante", cfg["variante"],
                      "--profundidad", str(cfg["profundidad"]))
    if paso == "preparar":
        revision = opciones.get("revision")
        return script("preparar.py", programa, *(["--forzar"] if opciones.get("forzar") else []),
                      *(["--revision", str(REVISIONES / revision)] if revision else []))
    if paso == "propuestas":
        return comando_claude(programa)
    if paso == "aplicar":
        return script("aplicar.py", programa)
    if paso == "subir":
        if not opciones.get("escribir"):
            return script("subir.py", programa)
        orden = opciones.get("orden", "").strip()
        return script("subir.py", programa, "--escribir", *(["--orden", orden] if orden else []),
                      *(["--activar"] if opciones.get("activar") else []))
    return None


def motivo_para_saltar(paso: str, programa: str) -> str:
    """Un paso que no tiene nada que hacer se salta en el flujo automático."""
    carpeta = TRABAJO / programa
    if paso == "propuestas":
        datos = leer_json(carpeta / "hallazgos.json", {})
        if not any(t["clasificacion"] in ("propuesta", "automatico") for t in datos.get("tareas", [])):
            return "no hay tareas para Claude"
    if paso == "aplicar" and not leer_json(carpeta / "propuestas.json", {}).get("propuestas") and \
            not any(t["clasificacion"] == "pseudocomentario"
                    for t in leer_json(carpeta / "hallazgos.json", {}).get("tareas", [])):
        return "no hay propuestas"
    if paso == "subir" and not any((carpeta / "corregido").glob("*.abap")):
        return "no hay código corregido"
    return ""


def flujo(programas_: list[str], pasos: list[str], opciones: dict) -> None:
    """Ejecuta los pasos en orden para cada programa; se detiene en el primer error de cada uno."""
    log, todo_ok, resumen = [], True, []
    with st.status("Flujo automático en curso…", expanded=True) as estado:
        if "revision" in pasos:
            st.write(f"▶ {PASOS['revision']}: {', '.join(programas_)}")
            cfg = settings()["atc"]
            ok, salida = correr(script("fase1_revision.py", *programas_, "--variante", cfg["variante"],
                                       "--profundidad", str(cfg["profundidad"])))
            log.append(f"=== {PASOS['revision']} · {'ok' if ok else 'ERROR'} ===\n{salida}")
            if not ok:
                estado.update(label="El flujo se detuvo en la revisión ATC", state="error")
                guardar_resultado("Flujo automático", False, "\n\n".join(log))
            resumen = [resumen_revision(p) for p in programas_]
            for linea in resumen:
                st.write(f"  {linea}")
            # Revisión nueva: el código de SAP puede haber cambiado
            opciones = {**opciones, "forzar": True}
        for prog in programas_:
            for paso in [p for p in PASOS if p in pasos and p != "revision"]:
                motivo = motivo_para_saltar(paso, prog)
                if motivo:
                    st.write(f"⏭ {prog} · {PASOS[paso]}: se salta ({motivo})")
                    log.append(f"=== {prog} · {PASOS[paso]} · se salta: {motivo} ===")
                    continue
                comando = comando_paso(paso, prog, opciones)
                if not comando:
                    ok, salida = False, "No se encontró el CLI de Claude Code (revisa interfaz.claude)."
                else:
                    st.write(f"▶ {prog} · {PASOS[paso]}")
                    ok, salida = correr(comando, timeout=3600 if paso == "propuestas" else 1800)
                log.append(f"=== {prog} · {PASOS[paso]} · {'ok' if ok else 'ERROR'} ===\n{salida}")
                if not ok:
                    todo_ok = False
                    st.write(f"✖ {prog} · {PASOS[paso]} falló; se detiene este programa")
                    break
        estado.update(label="Flujo terminado" if todo_ok else "Flujo terminado con errores",
                      state="complete" if todo_ok else "error")
    guardar_resultado("Flujo automático", todo_ok, "\n\n".join(log), resumen)


def contrasena_guardada() -> bool:
    import keyring
    from sap.rfc_client import SERVICIO, cuenta_credencial
    return keyring.get_password(SERVICIO, cuenta_credencial()) is not None


# ---------------------------------------------------------------- piezas de pantalla

def linea_etapas(info: dict) -> None:
    partes = [f"<span style='opacity:{1 if hecho else .45}'>{'✓' if hecho else '○'} {nombre}</span>"
              for nombre, hecho in etapas(info)]
    st.markdown(f"<div style='font-size:.85rem;margin:-.6rem 0 .6rem'>{' &nbsp;›&nbsp; '.join(partes)}</div>",
                unsafe_allow_html=True)


def mostrar_ultima() -> None:
    u = st.session_state.get("ultima")
    if u:
        for linea in u.get("resumen", []):
            st.info(linea)
        icono = "✅" if u["ok"] else "❌"
        with st.expander(f"{icono} {u['titulo']} · {u['hora']} · "
                         f"{'terminó bien' if u['ok'] else 'terminó con error'}", expanded=not u["ok"]):
            st.code(u["salida"], language="text")


def tabla_diff(original: list[str], nuevo: list[str], titulos: tuple[str, str],
               contexto: bool, alto: int | None = None, desde: int = 1) -> None:
    tabla = difflib.HtmlDiff(tabsize=2, wrapcolumn=95).make_table(
        original, nuevo, *titulos, context=contexto, numlines=4)
    if desde != 1:
        # HtmlDiff numera desde 1; se corrige para mostrar las líneas reales del include
        tabla = re.sub(r'(<td class="diff_header" id="(?:from|to)\d+_\d+">)(\d+)(</td>)',
                       lambda m: f"{m.group(1)}{int(m.group(2)) + desde - 1}{m.group(3)}", tabla)
    # Sin alto: la tabla ocupa lo que mide. Con alto: se limita y se desplaza dentro
    limite = f' style="max-height:{alto}px"' if alto else ""
    st.html(f'{ESTILO_DIFF}<div class="diffatc"{limite}>{tabla}</div>')


def selector_flujo(clave: str, incluir_revision: bool) -> tuple[list[str], dict, bool]:
    """Casillas de los pasos a encadenar; los marcados por defecto vienen de settings.yaml."""
    defecto = settings().get("interfaz", {}).get("automatico", ["preparar", "propuestas", "aplicar"])
    # "Armar tareas" va siempre con la revisión ATC y "Aplicar" con las propuestas: no son casillas aparte
    ayuda = {"revision": "Corre ATC en SAP y arma las tareas", "propuestas": "Claude propone y se aplica"}
    disponibles = [p for p in PASOS if p not in ("preparar", "aplicar") and (incluir_revision or p != "revision")]
    cols = st.columns(len(disponibles))
    pasos = [p for p, col in zip(disponibles, cols)
             if col.checkbox(PASOS[p], value=p in defecto, key=f"{clave}_{p}", help=ayuda.get(p))]
    if "revision" in pasos:
        pasos.insert(pasos.index("revision") + 1, "preparar")
    if "propuestas" in pasos:
        pasos.insert(pasos.index("propuestas") + 1, "aplicar")
    opciones, valido = {}, True
    if "subir" in pasos:
        opciones["escribir"] = st.toggle("Escribir en SAP (si está apagado, la subida solo se simula)",
                                         key=f"{clave}_escribir")
        if opciones["escribir"]:
            opciones["orden"] = st.text_input("Orden de transporte", key=f"{clave}_orden")
            opciones["activar"] = st.checkbox("Activar después de subir (solo si no hay errores de sintaxis)",
                                              key=f"{clave}_activar")
            valido = st.checkbox("Confirmo que el flujo escriba en SAP", key=f"{clave}_conf")
    return pasos, opciones, valido


def requiere_sap(pasos: list[str], opciones: dict) -> bool:
    return bool({"revision", "subir"} & set(pasos)) or (opciones.get("forzar") and "preparar" in pasos)


# ---------------------------------------------------------------- vistas

def barra_lateral() -> str:
    st.sidebar.title("ATC")
    etiquetas = {"Resumen general": "Resumen general"}
    con_pendientes, al_dia = ["Resumen general"], []
    for p in programas():
        info = cargar(p)
        hechas = [n for n, ok in etapas(info) if ok]
        pend, total = pendientes(info, p)
        cuenta = f"{pend}/{total} pendientes" if total else "sin hallazgos"
        etiquetas[p] = f"{p} :gray[· {cuenta} · {hechas[-1].lower() if hechas else 'nuevo'}]"
        (con_pendientes if pend else al_dia).append(p)

    # Dos grupos que funcionan como una sola selección: elegir en uno deselecciona el otro
    elegido = st.session_state.get("programa", "Resumen general")
    if elegido not in etiquetas:
        elegido = "Resumen general"
    grupos = [("Con pendientes", "grupo_pend", con_pendientes),
              ("Sin pendientes o sin hallazgos", "grupo_ok", al_dia)]

    def elegir(clave: str) -> None:
        st.session_state["programa"] = st.session_state[clave]

    for titulo, clave, opciones in grupos:
        if not opciones:
            continue
        st.session_state[clave] = elegido if elegido in opciones else None
        st.sidebar.radio(titulo, opciones, format_func=etiquetas.get, key=clave, on_change=elegir, args=(clave,))
    st.session_state["programa"] = elegido

    st.sidebar.divider()
    if contrasena_guardada():
        st.sidebar.caption("🔑 Contraseña SAP guardada")
    else:
        st.sidebar.warning("No hay contraseña SAP guardada; los pasos que leen de SAP quedarían esperando.")
        with st.sidebar.form("pwd"):
            pwd = st.text_input("Contraseña SAP", type="password")
            if st.form_submit_button("Guardar") and pwd:
                import keyring
                from sap.rfc_client import SERVICIO, cuenta_credencial
                keyring.set_password(SERVICIO, cuenta_credencial(), pwd)
                st.rerun()
    st.sidebar.caption(f"Modo de escritura: **{settings()['escritura']['modo']}**")
    return elegido


def verificacion_texto(info: dict, programa: str) -> str:
    verif = verificar(info, programa)
    if not verif:
        return "-"
    sigue, nuevos = verif
    alcance = [h for h in hallazgos_revision(info, programa) if en_alcance(h)]
    resueltos = sum(not sigue.get(clave(h), True) for h in alcance)
    nuevos_alc = sum(map(en_alcance, nuevos))
    return f"{resueltos}/{len(alcance)} resueltos" + (f", {nuevos_alc} nuevos" if nuevos_alc else "")


def seccion_documentacion() -> None:
    """Documentación de entrega de los programas elegidos, en pantalla para copiar y pegar."""
    st.subheader("Documentación")
    con_cambios = [p for p in programas() if leer_json(TRABAJO / p / "cambios.json", {}).get("cambios")]
    if not con_cambios:
        st.caption("Todavía no hay cambios aplicados para documentar.")
        return
    elegidos = st.multiselect("Programas", con_cambios, key="doc_programas",
                              placeholder="Elige los programas a documentar")
    if elegidos:
        mostrar_documentacion(elegidos)


def mostrar_documentacion(programas_: list[str]) -> None:
    """Entradas con los estilos del consolidado y un botón que las copia con formato."""
    import importlib

    import documentar
    importlib.reload(documentar)   # Streamlit no recarga solo los módulos importados al cambiarlos
    lista =[e for p in programas_ for e in documentar.entradas(p)]
    if not lista:
        st.caption("No hay cambios aplicados para documentar.")
        return
    contenido = documentar.a_html(lista)
    lineas = sum(len(b) for e in lista for b in e["codigo"]) + 6 * len(lista) \
        + sum(len(t) // 150 + 1 for e in lista for t in e["textos"])
    # El botón selecciona el bloque y lo copia como texto con formato (Word conserva fuentes y colores)
    components.html(f"""
      <button onclick="copiar()" style="font-family:sans-serif;padding:6px 14px;border:1px solid #ccc;
        border-radius:6px;background:#f7f7f7;cursor:pointer">Copiar</button>
      <span id="ok" style="font-family:sans-serif;font-size:12px;color:#2e7d32;margin-left:8px"></span>
      <div id="doc" style="background:#fff;padding:8px 12px;margin-top:8px;border:1px solid #e5e5e5">
        {contenido}
      </div>
      <script>
        function copiar() {{
          const r = document.createRange();
          r.selectNodeContents(document.getElementById('doc'));
          const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          document.execCommand('copy'); s.removeAllRanges();
          document.getElementById('ok').textContent = 'Copiado: pégalo en el consolidado';
        }}
      </script>""", height=min(900, 60 + 14 * lineas), scrolling=True)


def vista_resumen() -> None:
    st.header("Resumen general")
    mostrar_ultima()
    filas = []
    for p in programas():
        info = cargar(p)
        cuenta = {}
        for t in info["tareas"]:
            cuenta[t["estado"]] = cuenta.get(t["estado"], 0) + 1
        hechas = [n for n, ok in etapas(info) if ok]
        todos = hallazgos_revision(info, p)
        filas.append({
            "Programa": p,
            "Etapa": hechas[-1] if hechas else "-",
            "Revisión": info["revision"]["meta"]["fecha"] if info["revision"] else "-",
            "Hallazgos": len(todos),
            "En alcance": sum(map(en_alcance, todos)),
            "Tareas": len(info["tareas"]) if info["datos"] else "-",
            "Pendientes": cuenta.get("Pendiente", 0),
            "Propuestas": cuenta.get("Propuesto", 0) + cuenta.get("Propuesto (falta aplicar)", 0),
            "Aplicadas": cuenta.get("Aplicado", 0),
            "Subidas": cuenta.get("Subido (inactivo)", 0),
            "Sin cambio / info": cuenta.get("Sin cambio", 0) + cuenta.get("Informativo", 0),
            "ATC actual": verificacion_texto(info, p),
        })
    if filas:
        st.dataframe(filas, hide_index=True, width="stretch")
    else:
        st.info("Todavía no hay revisiones. Agrega programas abajo.")

    st.subheader("Procesar programas nuevos")
    texto = st.text_area("Programas (uno por línea)", height=100)
    st.caption("Pasos a ejecutar en cadena:")
    pasos, opciones, valido = selector_flujo("nuevo", incluir_revision=True)
    if st.button("▶ Ejecutar", type="primary"):
        nombres = list(dict.fromkeys(n.strip().upper() for n in texto.splitlines() if n.strip()))
        if not nombres or not pasos:
            st.warning("Escribe al menos un programa y elige al menos un paso.")
        elif not valido:
            st.warning("Marca la confirmación para escribir en SAP.")
        elif requiere_sap(pasos, opciones) and not contrasena_guardada():
            st.error("Guarda primero la contraseña SAP en la barra lateral.")
        else:
            flujo(nombres, pasos, opciones)

    seccion_documentacion()


def barra_acciones(programa: str, info: dict) -> None:
    """Acciones del programa, siempre visibles bajo el título."""
    sap_ok = contrasena_guardada()
    hay_corregido = any((info["carpeta"] / "corregido").glob("*.abap"))
    claude = comando_claude(programa)
    c1, c2, c3, c4, c5, c7, c6 = st.columns([1, 1.4, 1, 1.1, 1, 1.1, 1.3])

    with c1.popover("Rearmar tareas", width="stretch", disabled=not info["revision"]):
        st.caption("Vuelve a agrupar los hallazgos de la revisión con las reglas y prioridades actuales. "
                   "No consulta SAP. Rehace TAREA.md: las propuestas existentes pueden quedar desfasadas.")
        forzar = st.checkbox("Volver a descargar también el código de SAP (--forzar)")
        if st.button("Rearmar", disabled=forzar and not sap_ok):
            # Con la revisión con la que se preparó, no con una posterior de verificación
            base = (info["datos"] or {}).get("revision")
            guardar_resultado("Rearmar tareas", *correr(
                comando_paso("preparar", programa, {"forzar": forzar, "revision": base})))
    # Misma regla que el flujo automático: sin tareas para Claude o sin propuestas, no hay nada que hacer
    sin_claude = "programa sin preparar" if not info["datos"] else motivo_para_saltar("propuestas", programa)
    sin_aplicar = "programa sin preparar" if not info["datos"] else motivo_para_saltar("aplicar", programa)
    if c2.button("Procesar con Claude", width="stretch", disabled=not claude or bool(sin_claude),
                 help="No se encontró el CLI de Claude Code" if not claude
                 else f"Desactivado: {sin_claude}" if sin_claude else None):
        with st.spinner("Claude está procesando el programa; puede tardar varios minutos…"):
            ok, salida = correr(claude, timeout=3600)
            # Claude suele ejecutar aplicar.py; se ejecuta igual para dejar corregido\ al día
            if ok and not motivo_para_saltar("aplicar", programa):
                ok, salida_ap = correr(comando_paso("aplicar", programa, {}))
                salida += f"\n\n=== Aplicar · {'ok' if ok else 'ERROR'} ===\n{salida_ap}"
        guardar_resultado("Claude + Aplicar", ok, salida)
    if c3.button("Reaplicar", width="stretch", disabled=bool(sin_aplicar),
                 help=f"Desactivado: {sin_aplicar}" if sin_aplicar
                 else "Regenera corregido\\ desde propuestas.json (tras editarlo a mano o cambiar las marcas)"):
        guardar_resultado("Reaplicar", *correr(comando_paso("aplicar", programa, {})))
    if c4.button("Simular subida", width="stretch", disabled=not hay_corregido or not sap_ok):
        with st.spinner("Comparando con SAP…"):
            guardar_resultado("Simular subida", *correr(comando_paso("subir", programa, {})))
    with c5.popover("Subir a SAP", width="stretch", disabled=not hay_corregido or not sap_ok):
        orden = st.text_input("Orden de transporte", value=info["subida"].get("orden", ""))
        activar = st.checkbox("Activar después de subir",
                              help="Activa todos los includes subidos juntos, solo si se escribieron todos y el "
                                   "syntax check no dio errores. Las pantallas (dynpros) no se tocan.")
        confirmo = st.checkbox("Confirmo que quiero escribir en SAP")
        if st.button("Subir y activar" if activar else "Subir (inactivo)", type="primary", disabled=not confirmo):
            with st.spinner("Escribiendo en SAP…"):
                guardar_resultado("Subir a SAP", *correr(
                    comando_paso("subir", programa, {"escribir": True, "orden": orden, "activar": activar})))
    if c7.button("Verificar ATC", width="stretch", disabled=not sap_ok or not info["datos"],
                 help="Vuelve a correr ATC y compara con los hallazgos originales. "
                      "ATC revisa la versión activa: úsalo después de activar."):
        with st.spinner("Ejecutando ATC…"):
            ok, salida = correr(comando_paso("revision", programa, {}))
        guardar_resultado("Verificar ATC", ok, salida)
    with c6.popover("⚡ Flujo automático", width="stretch"):
        st.caption("Ejecuta en cadena los pasos marcados; se detiene en el primer error.")
        pasos, opciones, valido = selector_flujo(f"auto_{programa}", incluir_revision=True)
        if st.button("▶ Ejecutar flujo", type="primary", disabled=not pasos or not valido):
            if requiere_sap(pasos, opciones) and not sap_ok:
                st.error("Guarda primero la contraseña SAP en la barra lateral.")
            else:
                flujo([programa], pasos, opciones)
    if not sap_ok:
        st.caption("⚠️ Sin contraseña SAP guardada: los pasos que leen o escriben en SAP están desactivados.")


def tab_cambios(info: dict) -> None:
    cambios = info["cambios"].get("cambios", [])
    if not cambios:
        st.info("Todavía no hay cambios aplicados.")
        return
    if mtime(info["carpeta"] / "propuestas.json") > mtime(info["carpeta"] / "cambios.json"):
        st.warning("propuestas.json es más nuevo que los cambios aplicados: ejecuta Aplicar.")
    for c in cambios:
        mostrar_cambio(info, c)
        st.divider()


def mostrar_cambio(info: dict, c: dict) -> None:
    """Un cambio (de cambios.json o una propuesta sin aplicar): original contra propuesto,
    con unas líneas del código de alrededor para dar contexto."""
    rango = f"{c['linea_ini']}-{c['linea_fin']}" if c["tipo"] == "reemplazar" else f"tras {c['linea_ini']}"
    hallazgos = ", ".join(f"{h['id']} (P{h['prioridad']})" for h in c.get("hallazgos", [])) \
        or ", ".join(c.get("resuelve", []))
    st.markdown(f"**{c['id']}** · {c['include']} {rango} · confianza {c.get('confianza', '-')}"
                f" · resuelve {hallazgos or '-'}" + (f" · nota {c['nota']}" if c.get("nota") else ""))

    archivo = info["carpeta"] / "original" / f"{c['include'].replace('/', '#')}.abap"
    lineas = archivo.read_text(encoding="utf-8").splitlines() if archivo.exists() else []
    k = settings().get("interfaz", {}).get("contexto_cambio", 6)
    ini, fin = c["linea_ini"], c["linea_fin"]
    if c["tipo"] == "reemplazar":
        reemplazado, despues_de = lineas[ini - 1:fin], fin
    else:
        reemplazado, despues_de = [], ini
    desde = max(1, (ini if c["tipo"] == "reemplazar" else ini + 1) - k)
    antes = lineas[desde - 1:(ini - 1 if c["tipo"] == "reemplazar" else ini)]
    despues = lineas[despues_de:despues_de + k]
    if not lineas:   # sin el original descargado: solo el bloque, como antes
        antes, despues, reemplazado, desde = [], [], c.get("original", []), ini
    tabla_diff(antes + reemplazado + despues, antes + c["codigo_nuevo"] + despues,
               ("Original", "Propuesto"), contexto=False, desde=desde)
    st.markdown(c.get("justificacion", ""))
    if c.get("verificar"):
        st.warning(f"**Verificar:** {c['verificar']}")


def contexto_codigo(info: dict, include: str, linea: int | None, radio: int = 8) -> None:
    archivo = info["carpeta"] / "original" / f"{include.replace('/', '#')}.abap"
    if not archivo.exists() or not linea:
        st.caption("El código de este include no está descargado.")
        return
    lineas = archivo.read_text(encoding="utf-8").splitlines()
    ini, fin = max(1, linea - radio), min(len(lineas), linea + radio)
    st.code("\n".join(f"{n:>5}{'>' if n == linea else ' '}| {lineas[n - 1]}" for n in range(ini, fin + 1)),
            language="abap")


def tab_comparacion(info: dict) -> None:
    corregidos = sorted((info["carpeta"] / "corregido").glob("*.abap"))
    if not corregidos:
        st.info("No hay código en corregido\\.")
        return
    izq, der = st.columns([2, 1])
    archivo = izq.selectbox("Include", corregidos, format_func=lambda p: p.stem.replace("#", "/"))
    solo_cambios = der.toggle("Solo las zonas cambiadas", value=True)
    original = (info["carpeta"] / "original" / archivo.name).read_text(encoding="utf-8").splitlines()
    nuevo = archivo.read_text(encoding="utf-8").splitlines()
    tabla_diff(original, nuevo, ("Original (SAP)", "Corregido"), contexto=solo_cambios, alto=650)


CAMPOS_REF = ("ref_tipo", "ref_objeto", "componente", "categoria", "cambio", "nota_titulo", "pseudocomentario")


NOTA_EN_MENSAJE = re.compile(r"Note(?:\(s\))?:\s*0*(\d+)", re.IGNORECASE)


def nota_de(tarea: dict | None, h: dict) -> str:
    """Nota SAP del hallazgo: la de preparar (regla o mensaje); si no, la del mensaje o la de la tarea
    (es la que llevan las marcas)."""
    if h.get("nota"):
        return h["nota"]
    m = NOTA_EN_MENSAJE.search(h["mensaje"])
    return m.group(1) if m else (tarea or {}).get("nota", "")


def con_detalle(info: dict, h: dict) -> dict:
    """El hallazgo con los datos de simplificación, tomados de otra revisión si le faltan."""
    if h.get("ref_objeto") or firma(h) not in info["ref"]:
        return h
    otro = info["ref"][firma(h)]
    return {**h, **{c: otro.get(c, "") for c in CAMPOS_REF}}


def tab_hallazgos(info: dict, programa: str) -> None:
    """Tabla de hallazgos; al seleccionar una fila se muestra su corrección debajo."""
    todos = hallazgos_revision(info, programa)
    if not todos and not info["tareas"]:
        st.info("No hay hallazgos para este programa.")
        return
    pr = "/".join(f"P{p}" for p in sorted(prioridades())) or "todas"
    otros = sum(not en_alcance(h) for h in todos)
    ver_todos = st.toggle(f"Mostrar también los de otras prioridades ({otros})", key=f"todos_{programa}")

    # (tarea o None, hallazgo) por fila: los de las tareas y, si se pide, los que quedaron fuera
    refs = [(t, h) for t in info["tareas"] for h in t["hallazgos"] if ver_todos or en_alcance(h)]
    en_tareas = {clave(h) for t in info["tareas"] for h in t["hallazgos"]}
    refs += [(None, h) for h in todos if clave(h) not in en_tareas and (ver_todos or en_alcance(h))]
    if not refs:
        st.info(f"No hay hallazgos con las prioridades configuradas ({pr}).")
        return

    def estado(t, h):
        if t:
            return f"{ICONO.get(t['estado'], '')} {t['estado']}"
        return "⚫ Sin preparar" if en_alcance(h) and not info["datos"] else "⚪ Fuera de alcance"

    verif = verificar(info, programa)
    if verif:
        sigue, nuevos = verif
        resueltos = sum(not sigue.get(clave(h), True) for _, h in refs)
        nuevos_vis = [h for h in nuevos if ver_todos or en_alcance(h)]
        st.caption(f"ATC del {info['verificacion']['meta']['fecha']}: **{resueltos} de {len(refs)} resueltos**"
                   + (f" · {len(nuevos_vis)} hallazgos nuevos" if nuevos_vis else ""))

    # Columnas de la base de simplificaciones, solo si alguna fila las tiene (revisiones nuevas)
    refs = [(t, con_detalle(info, h)) for t, h in refs]
    con_ref = any(h.get("ref_objeto") for _, h in refs)
    filas = [{"Estado": estado(t, h),
              **({"ATC actual": "❌ sigue" if verif[0].get(clave(h)) else "✅ resuelto"} if verif else {}),
              "Hallazgo": h.get("id", "-"), "Prioridad": f"P{h['prioridad']}",
              "Include": h["include"], "Línea": h["linea"],
              **({"Ref. tipo": h.get("ref_tipo", ""), "Ref. objeto": h.get("ref_objeto", ""),
                  "Categoría": h.get("categoria", "")[:1], "Cambio": h.get("cambio", "").split(" (")[-1].rstrip(")")}
                 if con_ref else {}),
              "Nota": nota_de(t, h),
              "Corrección en": f"{t['include']}:{t['linea']}" if t else "-",
              "Mensaje": h["mensaje"]} for t, h in refs]
    evento = st.dataframe(filas, hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key=f"tabla_{programa}")
    # La selección puede quedar desfasada si el interruptor cambió las filas
    seleccion = [i for i in evento.selection.rows if i < len(refs)]
    if seleccion:
        detalle(info, *refs[seleccion[0]])
    else:
        st.caption("Selecciona una fila para ver su corrección.")
    if verif and nuevos_vis and st.toggle(f"Ver hallazgos nuevos ({len(nuevos_vis)})", key=f"nuevos_{programa}"):
        st.dataframe([{"Prioridad": f"P{h['prioridad']}", "Include": h["include"], "Línea": h["linea"],
                       "Mensaje": h["mensaje"]} for h in nuevos_vis], hide_index=True, width="stretch")


def detalle(info: dict, tarea: dict | None, h: dict) -> None:
    st.markdown(f"**{h.get('id', '')} · P{h['prioridad']}** · {h['include']} {h['linea']} · {h['mensaje']}")
    extra = [f"{h['ref_tipo']} {h['ref_objeto']}" if h.get("ref_objeto") else "",
             f"categoría {h['categoria']}" if h.get("categoria") else "",
             f"cambio {h['cambio']}" if h.get("cambio") else "",
             h.get("componente", ""), f"nota: {h['nota_titulo']}" if h.get("nota_titulo") else "",
             f"`{h['pseudocomentario']}`" if h.get("pseudocomentario") else ""]
    if any(extra):
        st.caption(" · ".join(e for e in extra if e))
    if tarea is None:
        motivo = "programa sin preparar" if not info["datos"] and en_alcance(h) else "fuera de las prioridades"
        st.caption(f"Sin corrección ({motivo}).")
        contexto_codigo(info, h["include"], h["linea"])
        return

    st.caption(f"{tarea['id']} · {tarea['regla']} · se corrige en {tarea['include']} {tarea['linea']}")
    cambios = [c for c in info["cambios"].get("cambios", []) if c["tarea"] == tarea["id"]]
    pendientes = [p for p in info["propuestas"].get("propuestas", []) if p["tarea"] == tarea["id"]]
    if cambios and tarea["estado"] != "Propuesto (falta aplicar)":
        for c in cambios:
            mostrar_cambio(info, c)
    elif pendientes:
        st.caption("Propuesta sin aplicar.")
        for p in pendientes:
            mostrar_cambio(info, p)
    elif tarea["motivo"]:
        st.caption(f"Sin cambio: {tarea['motivo']}")
    elif tarea["clasificacion"] == "reporte":
        st.caption("Informativo: no se corrige.")
        contexto_codigo(info, tarea["include"], tarea["linea"])
    else:
        st.caption("Sin corrección todavía.")
        contexto_codigo(info, tarea["include"], tarea["linea"])


def tab_dependencias(info: dict, programa: str) -> None:
    """Objetos relacionados que encontró la revisión (como ZDEPENDENCIAS) y sus hallazgos."""
    objetos = [o for o in (info["revision"] or {}).get("objetos", []) if programa in o.get("origenes", [])]
    if not objetos:
        st.caption("Sin revisión ATC: todavía no hay dependencias.")
        return
    por_include = {}
    for h in hallazgos_revision(info, programa):
        por_include[h["include"]] = por_include.get(h["include"], 0) + 1
    tipos = sorted({o["tipo"] for o in objetos})
    elegidos = st.multiselect("Tipo", tipos, default=tipos, key=f"dep_{programa}", label_visibility="collapsed")
    filas = [{"Nivel": o.get("nivel"), "Tipo": o["tipo"], "Nombre": o["nombre"], "Padre": o.get("padre") or "",
              "ATC": o.get("estado") or "-",
              # Solo objetos con código: una tabla puede llamarse igual que el programa
              "Hallazgos": por_include.get(o["nombre"], 0) or "" if o.get("tipo_adt") else ""}
             for o in sorted(objetos, key=lambda o: (o.get("nivel") or 0, o["tipo"], o["nombre"]))
             if o["tipo"] in elegidos]
    st.dataframe(filas, hide_index=True, width="stretch")


def tab_subida(info: dict) -> None:
    s = info["subida"]
    if not s:
        st.caption("Todavía no se subió nada a SAP.")
        return
    act = s.get("activacion")
    estado = "activado" if act and act.get("activado") else "inactivo"
    st.markdown(f"**{s.get('fecha', '-')}** · orden {s.get('orden') or '-'} · {estado}")
    for i in s.get("includes", []):
        st.markdown(f"- **{i['include']}**: {i['estado']}")
        for m in i.get("syntax_check", []):
            st.caption(f"    {m}")
    if act:
        for m in act.get("mensajes", []):
            st.caption(f"Activación: {m}")


def vista_programa(programa: str) -> None:
    info = cargar(programa)
    st.header(programa)
    linea_etapas(info)
    barra_acciones(programa, info)
    mostrar_ultima()
    pestanas = st.tabs(["Hallazgos", "Cambios propuestos", "Comparación completa", "Dependencias",
                        "Subida", "TAREA.md", "Documentación"])
    with pestanas[0]:
        tab_hallazgos(info, programa)
    with pestanas[1]:
        tab_cambios(info)
    with pestanas[2]:
        tab_comparacion(info)
    with pestanas[3]:
        tab_dependencias(info, programa)
    with pestanas[4]:
        tab_subida(info)
    with pestanas[5]:
        tarea = info["carpeta"] / "TAREA.md"
        st.markdown(tarea.read_text(encoding="utf-8") if tarea.exists() else "_Sin TAREA.md_")
    with pestanas[6]:
        mostrar_documentacion([programa])


def main():
    st.set_page_config(page_title="ATC", page_icon="🛠️", layout="wide")
    elegido = barra_lateral()
    if elegido == "Resumen general":
        vista_resumen()
    else:
        vista_programa(elegido)


main()

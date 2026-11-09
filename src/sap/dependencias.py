"""Obtiene los objetos relacionados de un objeto, replicando ZDEPENDENCIAS.

ZDEPENDENCIAS usa por dentro el módulo de función REPOSITORY_ENVIRONMENT_RFC,
que se puede llamar por RFC. Aquí recorremos el árbol nivel por nivel, igual
que el programa, pero solo expandimos los objetos con código.
"""
from collections import deque
from dataclasses import dataclass

FM_ENTORNO = "REPOSITORY_ENVIRONMENT_RFC"

# Objetos sobre los que se ejecuta ATC (lista blanca)
TIPOS_CON_CODIGO = {"PROG", "CLAS", "INTF", "FUGR"}

# Subobjetos que se reemplazan por el objeto que los contiene (ENCL_OBJ)
CONTENEDOR = {"FUNC": "FUGR", "METH": "CLAS"}

# Tipos que se descartan, igual que ZDEPENDENCIAS
DESCARTAR = {"MESS"}


@dataclass
class Objeto:
    tipo: str
    nombre: str
    nivel: int
    padre: str

    @property
    def clave(self) -> tuple:
        return (self.tipo, self.nombre)

    @property
    def para_atc(self) -> bool:
        return self.tipo in TIPOS_CON_CODIGO


def es_cliente(nombre: str) -> bool:
    return nombre.upper().startswith(("Z", "Y"))


def entorno(conn, tipo: str, nombre: str) -> list[dict]:
    """Llamada directa al módulo: devuelve las filas tal como vienen de SAP."""
    r = conn.call(FM_ENTORNO, OBJ_TYPE=tipo, OBJECT_NAME=nombre)
    return r.get("ENVIRONMENT_TAB", [])


def _normalizar(fila: dict) -> tuple[str, str] | None:
    tipo = str(fila.get("TYPE", "")).strip().upper()
    nombre = str(fila.get("OBJECT", "")).strip().upper()
    contenedor = str(fila.get("ENCL_OBJ", "")).strip().upper()
    if not tipo or not nombre or tipo in DESCARTAR:
        return None
    if tipo == "INCL":
        return ("PROG", nombre)
    if tipo in CONTENEDOR:
        if not contenedor:
            return (tipo, nombre)   # se reporta tal cual para revisarlo
        return (CONTENEDOR[tipo], contenedor)
    return (tipo, nombre)


def obtener_dependencias(conn, tipo: str, nombre: str, profundidad: int = 4) -> list[Objeto]:
    raiz = Objeto(tipo.upper(), nombre.upper(), 0, "")
    encontrados = {raiz.clave: raiz}
    cola = deque([raiz])

    while cola:
        actual = cola.popleft()
        if actual.nivel >= profundidad or not actual.para_atc:
            continue
        for fila in entorno(conn, actual.tipo, actual.nombre):
            norm = _normalizar(fila)
            if not norm or not es_cliente(norm[1]) or norm in encontrados:
                continue
            nuevo = Objeto(norm[0], norm[1], actual.nivel + 1, actual.nombre)
            encontrados[nuevo.clave] = nuevo
            cola.append(nuevo)

    return sorted(encontrados.values(), key=lambda o: (o.nivel, o.tipo, o.nombre))

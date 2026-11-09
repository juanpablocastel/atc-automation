# Proyecto: corrección de hallazgos ATC

Este proyecto corrige hallazgos ATC de programas ABAP de un cliente, sistema
S/4HANA 2023 (SAP_BASIS 816). Python obtiene los hallazgos y el código; tú (Claude)
propones las correcciones; Joseph las revisa y aprueba.

## Carpeta de trabajo de un programa

`trabajo\<PROGRAMA>\`
- `TAREA.md`: tareas agrupadas por lugar de corrección, con regla y contexto.
- `hallazgos.json`: los mismos datos en formato estructurado.
- `original\`: código descargado de SAP. **Nunca lo modifiques.**
- `propuestas.json`: **lo que tú escribes.**
- `corregido\`: código con marcas; lo genera `src\aplicar.py`, no lo edites a mano.

## Cuando Joseph diga "procesa trabajo\<PROGRAMA>"

1. Lee `TAREA.md` y los archivos de `original\` que necesites.
2. Escribe `propuestas.json` con el formato de abajo.
3. Si existen, ejecuta `python src\aplicar.py <PROGRAMA>` y `python src\validar.py <PROGRAMA>`,
   y corrige tus propuestas si hay errores.
4. Resume en el chat qué propusiste, qué no y qué debe verificar Joseph.

## Reglas de corrección

- Sigue la `instruccion` de cada tarea (viene de `config\reglas_atc.yaml`).
- Tareas con clasificación `reporte`: no propongas cambios; agrégalas en `sin_cambio`.
- Tareas con clasificación `pseudocomentario`: no hagas nada; las resuelve `aplicar.py`.
- Cambio mínimo: corrige solo el hallazgo, sin refactorizar ni cambiar nombres.
- Conserva el comportamiento del programa. Si una corrección puede cambiarlo,
  confianza `baja` y explícalo en `verificar`.
- Respeta el estilo del código existente (sintaxis clásica o moderna, sangría,
  mayúsculas). No mezcles estilos dentro de una misma sentencia.
- Las líneas que indiques son las del archivo de `original\` (empiezan en 1).
- **No escribas las marcas del cliente** (`*REMEDIACION ...`) ni comentes el código original:
  `aplicar.py` comenta las líneas del rango y agrega las marcas con la nota SAP.
- `codigo_nuevo` es el bloque completo que reemplaza al rango. Puede incluir líneas sin
  cambios si forman parte de la misma unidad lógica (por ejemplo, el `IF sy-subrc` que sigue).
- No tienes acceso a SAP. Si te falta un dato (por ejemplo la clave de una tabla),
  no lo inventes: confianza `baja` e indica qué verificar.
- Si la corrección va en un include que no está en `original\` (por ejemplo, la declaración
  de una variable en el TOP), descárgalo con `python src\leer_fuente.py <INCLUDE> include`
  y cópialo de `data\sources\` a `original\`. `aplicar.py` lo registra en
  `trabajo\_includes.json` y avisa si otro programa ya lo corrige.
- Las justificaciones van en español, claras para un documento de entrega.

## Formato de `propuestas.json`

```json
{
  "programa": "ZBT_CONTROLKIT",
  "propuestas": [
    {
      "id": "P01",
      "tarea": "T01",
      "include": "ZBT_CONTROLKIT",
      "tipo": "reemplazar",
      "linea_ini": 186,
      "linea_fin": 189,
      "codigo_nuevo": [
        "  SELECT matnr werks",
        "    FROM marc",
        "    INTO TABLE lt_marc",
        "    WHERE werks = p_werks",
        "    ORDER BY PRIMARY KEY."
      ],
      "resuelve": ["H03", "H09", "H10"],
      "justificacion": "Se agrega ORDER BY para que la lectura por índice posterior sea determinista.",
      "confianza": "alta",
      "verificar": ""
    }
  ],
  "sin_cambio": [
    {"tarea": "T05", "motivo": "Hallazgo informativo; la variable destino ya tiene longitud suficiente."}
  ]
}
```

- `tipo: "reemplazar"`: las líneas `linea_ini` a `linea_fin` se comentan y se agrega `codigo_nuevo`.
- `tipo: "insertar"`: `codigo_nuevo` se agrega después de `linea_ini`; usa `linea_fin` igual a `linea_ini`.
- Las propuestas de un mismo include no deben superponerse en sus rangos de líneas.

## Escritura en SAP

Mira `escritura.modo` en `config\settings.yaml`:
- `manual` o `rfc`: **nunca escribas en SAP**. Joseph lo hace con Eclipse o con `src\subir.py`.
- `claude_mcp` (cuando haya VPN): solo si Joseph lo pide explícitamente y después de aprobar
  las propuestas, escribe en SAP el contenido de `corregido\` con el MCP, **como versión
  inactiva**. Nunca actives, nunca toques objetos que no estén en la carpeta y ejecuta el
  syntax check después de escribir. Informa qué objetos escribiste.

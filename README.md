# atc-automation

Corrección asistida de hallazgos ATC en programas ABAP (S/4HANA 2023, SAP_BASIS 816).
Python obtiene los hallazgos y el código por RFC; Claude Code propone las correcciones;
se revisan y aprueban en la interfaz web y se suben a SAP.

## Instalación en una PC nueva

### 1. Programas necesarios

| Programa | Cómo |
|----------|------|
| Python 3.12 | `winget install Python.Python.3.12` |
| Git y GitHub CLI | `winget install Git.Git GitHub.cli` |
| Visual C++ Redistributable | `winget install Microsoft.VCRedist.2015+.x64` (lo necesita el SDK de SAP) |
| Claude Code | https://claude.com/claude-code (para el botón "Procesar con Claude") |
| SAP NW RFC SDK 7.50 | Ver paso 2 |

Después de instalar, **cierra y vuelve a abrir VS Code** para que reconozca los comandos nuevos.

### 2. SAP NW RFC SDK

El SDK no está en este repositorio (licencia de SAP).

1. Cópialo desde la PC anterior (`C:\_dev\nwrfc750P_20-70002755\nwrfcsdk`) o descárgalo del
   SAP Support Portal (*SAP NW RFC SDK 7.50*, Windows x64; requiere usuario S).
2. Déjalo en una carpeta, por ejemplo `C:\_dev\nwrfcsdk`, que debe contener `lib\sapnwrfc.dll`.
3. Crea la variable de entorno de usuario `SAPNWRFC_HOME` apuntando a esa carpeta:
   ```powershell
   [Environment]::SetEnvironmentVariable('SAPNWRFC_HOME', 'C:\_dev\nwrfcsdk', 'User')
   ```
   `src\sap\rfc_client.py` agrega `lib\` al PATH por su cuenta; no hace falta tocar el PATH.

### 3. Clonar e instalar dependencias

```powershell
gh auth login --web
gh repo clone juanpablocastel/atc-automation D:\_projects\IA\atc-automation
cd D:\_projects\IA\atc-automation
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4. Conexión a SAP

1. Copia la plantilla y complétala con los datos de la entrada de SAP Logon:
   ```powershell
   Copy-Item config\conexion.example.yaml config\conexion.local.yaml
   ```
   `conexion.local.yaml` **no se sube a git**. La contraseña no va en ese archivo.
2. Prueba la conexión. La primera vez pide la contraseña y la guarda en el
   Administrador de credenciales de Windows:
   ```powershell
   .venv\Scripts\python.exe src\test_conexion.py
   ```
   Debe mostrar `Conexión OK` con el sistema y el release.

### 5. Traer el trabajo en curso (opcional)

Estas carpetas tienen código y datos del cliente, por eso **no están en git**.
Si quieres continuar donde quedaste, cópialas desde la PC anterior (USB, OneDrive, etc.):

| Carpeta | Contenido |
|---------|-----------|
| `trabajo\` | Un subdirectorio por programa: original, propuestas, corregido |
| `data\` | Revisiones ATC, hallazgos y fuentes descargadas |
| `output\` | Reportes Excel |

### 6. Abrir la interfaz

```powershell
.venv\Scripts\python.exe -m streamlit run src\interfaz.py
```

Se abre en http://localhost:8501.

## Problemas conocidos

**"An Application Control policy has blocked this file"**: Windows tiene activado
*Smart App Control*, que bloquea ejecutables y DLL sin firma.
- Lanza las herramientas con `python -m ...` (como arriba), no con los `.exe` de `.venv\Scripts`.
- Si el error aparece al importar `pandas` o `pyarrow` (tablas de la interfaz), hay que
  desactivar Smart App Control en *Seguridad de Windows → Control de aplicaciones y navegador*.
  En muchas versiones de Windows no se puede volver a activar sin reinstalar.

**`test_conexion.py` dice que la contraseña no es válida**: se borró la guardada; vuelve a
ejecutarlo para ingresarla de nuevo.

**`ImportError` de `pyrfc` o DLL no encontrada**: revisa que `SAPNWRFC_HOME` apunte a la
carpeta que contiene `lib\sapnwrfc.dll` y abre una terminal nueva.

## Estructura

```
config\settings.yaml         configuración general (variante ATC, marcas del cliente, modo de escritura)
config\reglas_atc.yaml       reglas de corrección por tipo de hallazgo
config\conexion.local.yaml   datos de conexión (local, no versionado)
src\interfaz.py              interfaz web (Streamlit)
src\fase1_revision.py        ejecuta la revisión ATC
src\preparar.py              arma las tareas por programa en trabajo\
src\aplicar.py               aplica propuestas.json y genera corregido\
src\subir.py                 escribe el código corregido en SAP por RFC
src\documentar.py            genera la documentación de entrega
src\sap\                     cliente RFC, ATC, ADT y dependencias
CLAUDE.md                    instrucciones para Claude Code al procesar un programa
```

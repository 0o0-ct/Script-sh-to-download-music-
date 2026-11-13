# Changelog — OtterRip v7.3

Refactorización integral de `Down-Linux-music_v7.sh` (OtterRip v7.2 → v7.3),
centrada en seguridad, robustez, rendimiento, mantenibilidad y nuevas
funcionalidades.

- Compatibilidad: Bash 4.4+, Linux (pacman, apt, dnf, zypper, apk).
- Sin dependencias nuevas: solo `bash`, `python3`, `ffmpeg` y `yt-dlp`.
- La URL del backend y su SHA-256 **no** se han modificado.

---

## FASE 1 — Seguridad

### 1.1 Eliminada la ejecución de la caché de dependencias (`source` → parsing seguro)

`deps_cache_valid()` hacía `source "$DEPS_CACHE_FILE"`, lo que ejecutaba como
código un archivo escribible por el usuario: otro proceso o malware podía
conseguir ejecución arbitraria de comandos.

- Nueva función `load_deps_cache()`: lee el archivo línea a línea con `read` y
  asigna con `printf -v` (nunca `eval` ni `source`).
- Lista blanca estricta de claves (`DEPS_CACHE_KEYS`): `CACHED_OTTERRIP_VERSION`,
  `CACHED_LIB_DIR`, `CACHED_REQUIREMENTS_FILE`, `CACHED_PYBIN`,
  `CACHED_YTDLP_BIN`, `CACHED_SYNCEDLYRICS_AVAILABLE`, `CACHED_JS_ARGS`,
  `CACHED_TIMESTAMP`.
- Cualquier línea mal formada, con caracteres de control o con una clave no
  permitida invalida la caché completa.

### 1.2 Validación de las rutas de los binarios (`PYBIN`, `YTDLP_BIN`)

- Nueva función `validate_binary_path()`: exige ruta absoluta, fichero regular
  ejecutable y symlinks resueltos con `readlink -f`.
- El destino real debe estar dentro de `$VENV_DIR/bin/`, de una ruta de sistema
  conocida (`/usr/bin`, `/usr/local/bin`, `/opt`, …) o del `HOME` del usuario.
- Se aborta con error si el binario apunta a `/tmp`, `/var/tmp`, `/dev/shm`,
  `/run` o a un directorio escribible por cualquiera.
- Aplicada en `ensure_python_env()`, `ensure_ytdlp()`, `venv_pip_install()` y
  `deps_cache_valid()`.

### 1.3 Endurecida la validación del paquete del backend

`backend_archive_paths_are_safe()` ahora rechaza, además de las rutas
peligrosas:

- enlaces simbólicos y duros (`member.issym()` / `member.islnk()`);
- más de 500 miembros en el archivo;
- un tamaño total descomprimido superior a 50 MB (protección frente a *zip
  bombs*).

### 1.4 Archivos temporales protegidos contra ataques por symlink

- Nueva función `secure_mktemp()`: verifica que el directorio padre no sea
  escribible por grupo ni por otros, usa una plantilla con el PID (`$$`) y
  comprueba que el fichero creado es regular y no un symlink (`[[ -f && ! -L ]]`).
- `save_config()` y `write_deps_cache()` escriben de forma atómica (temporal +
  `mv`) en directorios privados.
- `venv_pip_install()` ya no escribe su log en `/tmp`: lo deja en el directorio
  de estado (modo 700).
- La sesión de descarga (`_download_job_body`) se crea en un directorio con
  permisos 700.

### 1.5 Inyección de argumentos vía URL

- Nueva función `is_safe_url()`: rechaza cualquier URL que no sea `http(s)` o
  que empiece por `-` (yt-dlp la interpretaría como una opción).
- Todas las invocaciones de yt-dlp pasan la URL tras `--`:
  `_parallel_worker`, `run_sequential_download`, `run_sequential_polled`,
  `count_playlist_entries` y el módulo de detección.
- Verificado: `yt-dlp -- "--exec=..."` trata el valor como URL (y lo rechaza)
  en lugar de ejecutarlo.

### 1.6 Bloqueo de destino con verificación TOCTOU

- `acquire_dest_lock()` compara la identidad (`dispositivo`/`inode`) del
  descriptor antes y después de `flock`: si el archivo de bloqueo fue
  sustituido, se aborta.
- Si `flock` no está disponible, se usa un bloqueo atómico por directorio
  (`mkdir`), en lugar de omitir la protección.

---

## FASE 2 — Robustez y manejo de errores

| # | Cambio |
|---|-------|
| 2.1 | `draw_download_progress()` valida que todos los contadores sean enteros no negativos y sale con `(( total <= 0 )) && return 0` (sin división por cero). |
| 2.2 | Nueva función `resolve_path()` con degradación `realpath -m` → `readlink -f` → `cd`+`pwd` → canonicalización en Bash puro. Sustituye a todas las llamadas directas a `realpath` en `resolve_destination`, `ask_path`, `menu_cover` y `menu_cookies`. |
| 2.3 | `detect_url_type()` se ejecuta bajo un límite global de 65 s (`run_with_timeout`, con `timeout` del sistema y vigilancia propia como respaldo). Si se agota, se marca `DETECTION_FALLBACK` y se recurre a la detección por URL. |
| 2.4 | `SESSION_PRINT_TEMPLATE` se valida al inicio de `main()`: solo caracteres imprimibles y los tokens `%(…)j` esperados; si está corrupta se restaura el valor por defecto. |
| 2.5 | `trap '' PIPE` global: un pipe cerrado ya no mata el script en silencio. En `run_sequential_download()` el estado real de yt-dlp se toma de `PIPESTATUS[0]`. |
| 2.6 | `recover_backend_install()` usa `nullglob` y solo toca directorios con el nombre exacto que genera el script (`^\\.v[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+\\.[A-Za-z0-9]+$`). |
| 2.7 | `queue_run()` valida que el ID devuelto por `py_core queue claim` sea un entero positivo y muestra un error claro si la consulta a la cola falla. |
| 2.8 | `write_deps_cache()` escribe en un temporal del mismo directorio y renombra con `mv` (evita lecturas a medias). |

---

## FASE 3 — Rendimiento

- **3.1** Caché de `count_playlist_entries()` por hash SHA-256 de la URL
  (`URL_COUNT_CACHE`), con caducidad de 5 minutos (`URL_COUNT_TTL`).
- **3.2** El tamaño total de los logs del workers se calcula con **una sola**
  llamada a `wc -c` (en lugar de un `stat` por log) y el sondeo del progreso
  pasa de 1 a 2 segundos (`PROGRESS_POLL_INTERVAL`).
- **3.3** Los helpers `msg_info/msg_ok/msg_warn/msg_err` unen sus argumentos
  con `"$@"` (conservando los espacios internos) sin lanzar subshells.
- **3.4** `post_process_session()` reparte la metadata en lotes paralelos con
  `xargs -P` cuando hay más de 4 pistas verificadas. El manifiesto de sesión
  se divide en lotes, por lo que **no ha sido necesario modificar el backend
  Python** (cada lote es un manifiesto de sesión independiente).

---

## FASE 4 — Mantenibilidad y deuda técnica

- **4.1** La detección de URL se extrajo a `tools/detect_url.py` (módulo con
  argumentos de línea de comandos). El heredoc embebido se conserva como
  respaldo automático si el módulo no está disponible.
- **4.2** Cabeceras de documentación (`@brief`, `@param`, `@return`,
  `@side-effects`) en 38 funciones, con prioridad en seguridad, bloqueos y
  descarga.
- **4.3** Nuevo esquema `CONFIG_SCHEMA` (asociativo) que sustituye al `case`
  gigante de `load_config()`: cada clave declara tipo, validador y valor por
  defecto. Se añadieron `config_validate_value()` y `config_apply_value()`.
- **4.4** Constantes en sección dedicada y `readonly`: `MAX_LOG_SIZE`,
  `MIN_DISK_SPACE_KB`, `WARN_DISK_SPACE_KB`, `MAX_WORKERS`,
  `BACKEND_MAX_SIZE_MB`, `BACKEND_MAX_MEMBERS`, `LOCK_TIMEOUT`,
  `SESSION_EXPIRY_DAYS`, `DEPS_CACHE_TTL`, `URL_COUNT_TTL`, `DETECT_TIMEOUT`,
  `DETECT_PY_TIMEOUT` y `PROGRESS_POLL_INTERVAL`. Todos los números mágicos
  del código se han sustituido por estas constantes.
- **4.5** Nuevo modo `--dry-run`: muestra URL, tipo, destino, plantilla,
  perfil, duración, workers, letras y todos los flags de yt-dlp sin descargar
  nada ni crear archivos.
- **4.6** Nuevos flags `--verbose` (muestra los comandos completos de yt-dlp) y
  `--debug` (`set -x`, vuelco del estado interno y conservación de los
  temporales). `log_debug()` y `dump_state()` son los helpers asociados.
- **4.7** Nueva suite de pruebas BATS `tests/test_otterrip.bats` con **79
  pruebas** que cubren `safe_name`, `resolve_destination`, `normalize_jobs`,
  `is_positive_int`, `apply_quality_choice`, el ciclo `load_config`/
  `save_config`, `build_common_flags`, la caché de dependencias (incluida la
  prueba de que **no ejecuta** su contenido), la validación de la plantilla de
  sesión, el proxy y las utilidades de rutas.

Corrección adicional detectada durante las pruebas: `declare -A` dentro de una
función crea una variable **local**, por lo que al sourcear el script desde
una función los mapas asociativos (`CONFIG_SCHEMA`, `URL_COUNT_CACHE`) se
perdían. Ahora se declaran con `declare -gA`.

### Corrección crítica: las letras no se incrustaban

**Síntoma:** tras descargar una lista o un álbum, ninguna pista quedaba con
letras incrustadas (y los metadatos tampoco se reparaban).

**Causa raíz:** el backend busca, en el manifiesto de sesión, el identificador
de vídeo **dentro del nombre del archivo**:

```python
for vid_id in session_ids:
    for af in audio_files:
        if vid_id in af.name:      # ← espera un ID suelto
```

pero la plantilla de sesión genera un JSON rico
(`{"filepath":"…","title":"…","id":"…"}`). Como la línea completa nunca aparece
dentro del nombre del archivo, `verify-session` no encontraba nada y marcaba
**todas** las pistas como defectuosas:

```
Descargados y verificados: 0
Defectuosos (reintentables): 10
```

Con `JOB_OK = 0`, `post_process_session()` terminaba antes de tiempo
(`(( JOB_OK == 0 )) && return 0`) y **nunca se ejecutaba** el paso de letras
ni el de reparación de metadatos.

**Solución:** nueva función `session_to_ids()`, que normaliza el manifiesto de
sesión (JSON o ID suelto) a un fichero con un ID por línea antes de invocar al
backend. Se aplica en `post_process_session()`, de modo que también funcionan
las sesiones interrumpidas de versiones anteriores.

La corrección se aplica **en la capa Bash**, sin tocar el backend: el paquete
publicado y su SHA-256 siguen siendo válidos y el arreglo beneficia por igual
a quien usa el repositorio y a quien descarga solo el `.sh`.

**Resultado tras la corrección** (descarga real de una lista de 19 vídeos):

| | Antes | Después |
|---|---|---|
| Descargados y verificados | 0 | 10 |
| Defectuosos | 10 | 0 |
| Metadatos reparados | *(omitido)* | 10 |
| Letras incrustadas | *(omitido)* | 2 (las que existen en LRCLIB) |
| Estado final | ERROR RECUPERABLE | ÉXITO |

Se han añadido 6 pruebas de regresión (`session_to_ids` y una prueba de
extremo a extremo contra `verify-session`).

---

## FASE 5 — Funcionalidad nueva

- **5.1 Proxy.** Variable `OTTERRIP_PROXY` y flag `--proxy <url>`
  (http/https/socks4/socks5). Se valida el formato, se guarda en la
  configuración persistente y se pasa a yt-dlp como `--proxy`.
- **5.2 Notificaciones.** Al terminar una descarga se envía un aviso con el
  resumen (`notify-send` en Linux, `terminal-notifier` u `osascript` en
  macOS). Controlable con `OTTERRIP_NOTIFY=true|false`.
- **5.3 Modo `--batch <archivo>`.** Procesa una lista de URLs (una por línea;
  se ignoran las vacías y las que empiezan por `#`). Admite el formato
  `URL [destino]` y muestra un resumen final.
- **5.4 Historial de descargas.** `$OTTERRIP_DATA_DIR/history.tsv` con las
  columnas `timestamp`, `url`, `title`, `dest`, `status` y `duration_secs`.
  Nueva opción de menú (17) y flag `--history`.
- **5.5 Verificación de actualizaciones.** Al iniciar (solo con terminal
  interactivo) se consulta la última versión publicada en GitHub y se avisa sin
  bloquear. Controlable con `OTTERRIP_CHECK_UPDATES=true|false`.

---

## FASE 6 — Verificación

| Comprobación | Resultado |
|---|---|
| `bash -n Down-Linux-music_v7.sh` | Sin errores |
| `shellcheck -x -s bash Down-Linux-music_v7.sh` | **0 errores y 0 avisos** |
| `bats tests/test_otterrip.bats` | 79/79 pruebas correctas |
| `PYTHONPATH=lib python3 -m unittest discover -s tests` | 34/34 pruebas correctas |
| `--version`, `--help`, `--check-deps` | Correctos |
| Descarga real (YouTube, secuencial y paralela) | Correcta |
| `set -Eeuo pipefail` | Sin salidas prematuras en los flujos probados |

Notas de shellcheck: no queda ningún aviso. Los casos que la versión original
silenciaba con directivas `# shellcheck disable=` se han resuelto en el código
en lugar de ocultarse.

---

## Compatibilidad

- Se mantiene la licencia y los créditos originales.
- No se ha modificado la URL del backend ni su SHA-256.
- No se ha eliminado ninguna funcionalidad existente: solo se ha mejorado o
  ampliado.
- `tools/detect_url.py` es una herramienta del script de arranque y vive
  **fuera de `lib/`** a propósito: `lib/` es exactamente el contenido del
  paquete del backend (`REQUIRED_FILES` y el digest publicado permanecen
  intactos), de modo que reconstruir el paquete no invalida el SHA-256.

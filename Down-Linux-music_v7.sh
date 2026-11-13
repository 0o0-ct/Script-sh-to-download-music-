#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  OtterRip v7.3 — Descargador de audio + letras
#  Basado en OtterRip v7.0 (misma filosofía y licencia)
#
#  Novedades v7.2 (ver README.md y CHANGELOG.md):
#   · Entorno virtual aislado (sin --break-system-packages)
#   · Verificación real de integridad tras cada descarga
#   · Sesiones persistentes y recuperables tras interrupciones
#   · Reintentos acotados y clasificación de errores
#   · Cola de descargas, biblioteca, reparador, letras avanzadas,
#     conversor de audio local y diagnóstico
#   · Lógica de archivos en Python testeable (lib/otterrip_core)
# ═══════════════════════════════════════════════════════════════
set -Eeuo pipefail

# ── Constantes ─────────────────────────────────────────────────
# Límites usados en varias partes del script (evitan números mágicos).
readonly MAX_LOG_SIZE=1048576           # Tamaño máximo del log antes de rotarlo (bytes)
readonly MIN_DISK_SPACE_KB=20480        # Espacio mínimo libre para descargar (KB)
readonly WARN_DISK_SPACE_KB=102400       # Espacio libre a partir del cual se avisa (KB)
readonly MAX_WORKERS=32                 # Máximo de descargas/letras simultáneas
readonly BACKEND_MAX_SIZE_MB=50         # Tamaño máximo descomprimido del backend (MB)
readonly BACKEND_MAX_MEMBERS=500        # Máximo de miembros en el tar del backend
readonly LOCK_TIMEOUT=120               # Segundos de espera máxima por un bloqueo
readonly SESSION_EXPIRY_DAYS=14         # Días antes de borrar una sesión vacía
readonly DEPS_CACHE_TTL=86400           # Validez de la caché de dependencias (s)
readonly URL_COUNT_TTL=300              # Validez de la caché de conteo de playlist (s)
readonly DETECT_TIMEOUT=65              # Límite global de detección de URL (s)
readonly DETECT_PY_TIMEOUT=50           # Límite interno del subprocess de detección (s)
readonly PROGRESS_POLL_INTERVAL=2       # Segundos entre sondeos del progreso paralelo

# SIGPIPE ignorado: así un pipe cerrado (p. ej. `| head -1`) no mata el script
# de forma silenciosa ni deja procesos huérfanos a medias.
trap '' PIPE

OTTERRIP_VERSION="7.3.4"
OTTERRIP_REPO="0o0-ct/Script-sh-to-download-music-"

if (( BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4) )); then
    printf 'OtterRip necesita Bash 4.4 o superior (detectado %s).\n' "$BASH_VERSION" >&2
    printf '  Alpine: apk add bash   ·   macOS no está soportado.\n' >&2
    exit 1
fi
if [[ -z "${HOME:-}" ]]; then
    printf 'La variable HOME no está definida.\n' >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
LIB_DIR="$SCRIPT_DIR/lib"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
BACKEND_SHA256="46dbc55a799552b5340aef38a390a457db7ed15d81df872a6225fe5539662adf"
BACKEND_ARCHIVE_URL="https://raw.githubusercontent.com/0o0-ct/Script-sh-to-download-music-/v${OTTERRIP_VERSION}/packages/otterrip-backend-v${OTTERRIP_VERSION}.tar.gz"

# ── Directorios XDG (los valores relativos se ignoran, según la especificación)
xdg_dir() {
    local var="${1-}" def="${2-}" val=""
    val="${!var:-}"
    if [[ "$val" == /* ]]; then
        printf '%s' "$val"
    else
        printf '%s/%s' "$HOME" "$def"
    fi
}
OTTERRIP_CONFIG_DIR="${OTTERRIP_CONFIG_DIR:-$(xdg_dir XDG_CONFIG_HOME .config)/otterrip}"
OTTERRIP_DATA_DIR="${OTTERRIP_DATA_DIR:-$(xdg_dir XDG_DATA_HOME .local/share)/otterrip}"
OTTERRIP_STATE_DIR="${OTTERRIP_STATE_DIR:-$(xdg_dir XDG_STATE_HOME .local/state)/otterrip}"
OTTERRIP_CACHE_DIR="${OTTERRIP_CACHE_DIR:-$(xdg_dir XDG_CACHE_HOME .cache)/otterrip}"
DEPS_CACHE_FILE="$OTTERRIP_CACHE_DIR/deps_verified.env"
COOKIES_BROWSER="${OTTERRIP_COOKIES_BROWSER:-}"
COOKIES_FILE="${OTTERRIP_COOKIES_FILE:-}"
export OTTERRIP_CONFIG_DIR OTTERRIP_DATA_DIR OTTERRIP_STATE_DIR OTTERRIP_CACHE_DIR

VENV_DIR="$OTTERRIP_DATA_DIR/venv"
APP_ROOT="$OTTERRIP_DATA_DIR/app"
APP_INSTALL_DIR="$APP_ROOT/v$OTTERRIP_VERSION"
CONFIG_FILE="$OTTERRIP_CONFIG_DIR/config"
LOG_FILE="$OTTERRIP_STATE_DIR/otterrip.log"
SESSIONS_DIR="$OTTERRIP_STATE_DIR/sessions"

# Carpeta de descargas: junto al script si es escribible; si no (instalación
# de sistema), en ~/Music/OtterRip.
if [[ -n "${OTTERRIP_DOWNLOADS:-}" ]]; then
    DOWNLOADS="$OTTERRIP_DOWNLOADS"
elif [[ -w "$SCRIPT_DIR" ]]; then
    DOWNLOADS="$SCRIPT_DIR/downloads"
else
    DOWNLOADS="$HOME/Music/OtterRip"
fi
DOWNLOADS="${DOWNLOADS%/}"
[[ -n "$DOWNLOADS" ]] || DOWNLOADS="$SCRIPT_DIR/downloads"

# Crea los directorios de runtime y ajusta permisos. Solo se invoca desde
# main() después de procesar --help/--version, para que hacer `source` del
# script no produzca efectos secundarios en el entorno del llamante.
init_runtime_dirs() {
    mkdir -p -- "$OTTERRIP_CONFIG_DIR" "$OTTERRIP_DATA_DIR" "$OTTERRIP_STATE_DIR/sessions" \
        "$OTTERRIP_CACHE_DIR" "$DOWNLOADS" 2>/dev/null || true
    chmod 700 -- "$OTTERRIP_CONFIG_DIR" "$OTTERRIP_DATA_DIR" "$OTTERRIP_STATE_DIR" 2>/dev/null || true
}

# Helpers de salida basados en printf (evitan que secuencias como \n o \c en
# variables de usuario sean interpretadas por echo -e).
# Los argumentos se unen con un espacio (sin perder los espacios internos).
MSG_TEXT=""
_msg_join() {
    MSG_TEXT=""
    local a=""
    for a in "$@"; do
        if [[ -z "$MSG_TEXT" ]]; then
            MSG_TEXT="$a"
        else
            MSG_TEXT+=" $a"
        fi
    done
}

msg_info() { _msg_join "$@"; printf '  %sℹ%s %s\n' "${CYAN:-}" "${NC:-}" "$MSG_TEXT"; }
msg_ok()   { _msg_join "$@"; printf '  %s✔%s %s\n' "${GREEN:-}" "${NC:-}" "$MSG_TEXT"; }
msg_warn() { _msg_join "$@"; printf '  %s⚠%s %s\n' "${YELLOW:-}" "${NC:-}" "$MSG_TEXT"; }
msg_err()  { _msg_join "$@"; printf '  %s✘%s %s\n' "${RED:-}" "${NC:-}" "$MSG_TEXT" >&2; }

# Registro de depuración (solo con --debug).
log_debug() {
    (( VERBOSITY >= 2 )) || return 0
    printf '[debug] %s\n' "$*" >&2
}

# Vuelca el estado interno del script (solo con --debug).
dump_state() {
    (( VERBOSITY >= 2 )) || return 0
    {
        printf '[debug] versión=%s verbosidad=%s dry-run=%s\n' \
            "$OTTERRIP_VERSION" "$VERBOSITY" "$DRY_RUN"
        printf '[debug] perfil=%s formato=%s calidad=%s\n' "$PROFILE" "$AFMT" "$AQ"
        printf '[debug] workers=%s duración=%s-%s\n' "$PARALLEL_JOBS" "$DUR_MIN" "$DUR_MAX"
        printf '[debug] destino=%s\n' "${LAST_DOWNLOAD_FOLDER:-}"
        printf '[debug] proxy=%s cookies=%s\n' "${OTTERRIP_PROXY:-ninguno}" "${COOKIES_BROWSER:-${COOKIES_FILE:-ninguno}}"
        printf '[debug] python=%s yt-dlp=%s\n' "${PYBIN:-}" "${YTDLP_BIN:-}"
    } >&2
    return 0
}

# ── Utilidades de rutas y permisos ─────────────────────────────

# Devuelve los permisos en octal de una ruta (vacío si no se puede leer).
path_mode() {
    local p="${1-}" m=""
    [[ -n "$p" ]] || return 1
    m="$(stat -c '%a' -- "$p" 2>/dev/null || true)"
    if [[ -z "$m" ]]; then
        m="$(stat -f '%Lp' -- "$p" 2>/dev/null || true)"
    fi
    [[ -n "$m" ]] || return 1
    printf '%s' "$m"
}

# El directorio es escribible por "otros" (peligro: cualquiera puede escribir).
# @brief Indica si un directorio es escribible por "otros".
# @param $1 Ruta del directorio.
# @return 0 si es escribible por cualquiera; 1 en caso contrario.
# @side-effects Ninguno.
is_world_writable_dir() {
    local d="${1-}" mode=""
    [[ -n "$d" && -d "$d" ]] || return 1
    mode="$(path_mode "$d")" || return 1
    case "${mode: -1}" in
        [2367]) return 0 ;;
    esac
    return 1
}

# El directorio no es escribible ni por grupo ni por "otros" (uso privado).
# @brief Indica si un directorio no es escribible por grupo ni por otros.
# @param $1 Ruta del directorio.
# @return 0 si es privado; 1 si no lo es o no se pueden leer sus permisos.
# @side-effects Ninguno.
is_private_dir() {
    local d="${1-}" mode=""
    [[ -n "$d" && -d "$d" ]] || return 1
    mode="$(path_mode "$d")" || return 1
    case "${mode: -2:1}" in
        [2367]) return 1 ;;
    esac
    case "${mode: -1}" in
        [2367]) return 1 ;;
    esac
    return 0
}

# Canonicaliza una ruta en Bash puro (elimina "." y ".." sin tocar el disco).
# @brief Canonicaliza una ruta en Bash puro (elimina "." y "..").
# @param $1 Ruta a canonicalizar.
# @return 0 e imprime la ruta canónica.
# @side-effects Ninguno.
canonicalize_path() {
    local p="${1-}" out="" abs="" part=""
    local -a parts=() stack=()
    [[ "$p" == /* ]] && abs="1"
    local IFS='/'
    read -r -a parts <<< "$p" || true
    for part in "${parts[@]}"; do
        case "$part" in
            ""|".") continue ;;
            "..")
                ((${#stack[@]} > 0)) && unset 'stack[-1]'
                ;;
            *) stack+=("$part") ;;
        esac
    done
    out="$(IFS=/; printf '%s' "${stack[*]}")"
    if [[ "$abs" == "1" ]]; then
        printf '/%s' "$out"
    else
        printf '%s' "$out"
    fi
}

# Resuelve una ruta a forma absoluta: realpath -m → readlink -f → cd+pwd →
# canonicalización en Bash puro. Nunca falla si la ruta es válida.
# @brief Resuelve una ruta a formato absoluto con degradación de métodos.
# @param $1 Ruta a resolver (absoluta o relativa).
# @return 0 e imprime la ruta resuelta; 1 si la ruta está vacía.
# @side-effects Ninguno (no modifica el sistema de archivos).
resolve_path() {
    local p="${1-}" r="" dir="" base="" abs=""
    [[ -n "$p" ]] || return 1
    if command -v realpath >/dev/null 2>&1; then
        r="$(realpath -m -- "$p" 2>/dev/null || true)"
        if [[ -n "$r" ]]; then
            printf '%s' "$r"
            return 0
        fi
    fi
    if command -v readlink >/dev/null 2>&1; then
        r="$(readlink -f -- "$p" 2>/dev/null || true)"
        if [[ -n "$r" ]]; then
            printf '%s' "$r"
            return 0
        fi
    fi
    dir="$(dirname -- "$p")"
    base="$(basename -- "$p")"
    if [[ -d "$dir" ]]; then
        abs="$(cd -- "$dir" 2>/dev/null && pwd -P)" || abs=""
        if [[ -n "$abs" ]]; then
            if [[ "$abs" == "/" ]]; then
                printf '/%s' "$base"
            else
                printf '%s/%s' "$abs" "$base"
            fi
            return 0
        fi
    fi
    canonicalize_path "$p"
}

# Crea un archivo temporal seguro: directorio padre privado, nombre con PID y
# verificación de que es un archivo regular (nunca un symlink).
# @brief Crea un archivo temporal seguro dentro de un directorio privado.
# @param $1 Directorio destino (debe existir y no ser escribible por otros).
# @param $2 Nombre base del temporal (por defecto "otterrip").
# @return 0 e imprime la ruta del archivo; 1 si no se pudo crear de forma segura.
# @side-effects Crea un archivo con permisos 600 en el directorio indicado.
secure_mktemp() {
    local dir="${1-}" pattern="${2:-otterrip}" tmp=""
    [[ -n "$dir" ]] || return 1
    [[ -d "$dir" ]] || mkdir -p -- "$dir" 2>/dev/null || return 1
    is_private_dir "$dir" || return 1
    tmp="$(mktemp "$dir/.${pattern}.$$.XXXXXX" 2>/dev/null)" || return 1
    if [[ ! -f "$tmp" || -L "$tmp" ]]; then
        rm -f -- "$tmp" 2>/dev/null || true
        return 1
    fi
    chmod 600 -- "$tmp" 2>/dev/null || true
    printf '%s' "$tmp"
}

# Comprueba que un binario sea seguro de ejecutar: ruta absoluta, ejecutable,
# sin symlinks hacia directorios escribibles por cualquiera (/tmp, /dev/shm...).
# @brief Comprueba que una ruta de binario sea segura de ejecutar.
# @param $1 Ruta absoluta del binario candidato.
# @return 0 si el binario es válido; 1 si es relativo, no ejecutable, apunta a
#         un directorio escribible por cualquiera o está fuera de rutas conocidas.
# @side-effects Ninguno.
validate_binary_path() {
    local bin="${1-}" real="" dir=""
    [[ -n "$bin" ]] || return 1
    [[ "$bin" == /* ]] || return 1
    [[ -f "$bin" && -x "$bin" ]] || return 1
    real="$(readlink -f -- "$bin" 2>/dev/null || printf '%s' "$bin")"
    [[ -n "$real" && -f "$real" && -x "$real" ]] || return 1
    dir="$(dirname -- "$real")"
    is_world_writable_dir "$dir" && return 1
    case "$real" in
        /tmp/*|/var/tmp/*|/dev/shm/*|/run/*) return 1 ;;
    esac
    case "$real" in
        "$VENV_DIR"/bin/*) return 0 ;;
        /usr/bin/*|/usr/sbin/*|/usr/local/bin/*|/usr/local/sbin/*|/bin/*|/sbin/*|/opt/*|/snap/bin/*) return 0 ;;
        "$HOME"/*) return 0 ;;
    esac
    return 1
}

# Comprueba que una URL sea segura para yt-dlp: debe ser http(s) y no puede
# empezar por "-" (si no, yt-dlp la interpretaría como una opción).
# @brief Indica si una URL puede pasarse a yt-dlp sin riesgo de inyección.
# @param $1 URL candidata.
# @return 0 si es http(s) y no empieza por "-"; 1 en caso contrario.
# @side-effects Ninguno.
is_safe_url() {
    local u="${1-}"
    [[ -n "$u" ]] || return 1
    [[ "$u" != -* ]] || return 1
    [[ "$u" != *[[:space:]]* ]] || return 1
    is_http_url "$u"
}

# Normaliza y valida la URL de un proxy (http, https, socks4, socks5...).
# @brief Normaliza y valida la URL de un proxy.
# @param $1 URL del proxy.
# @return 0 si el proxy es válido e imprime la URL normalizada; 1 si no lo es.
# @side-effects Ninguno.
normalize_proxy() {
    local p="${1-}" scheme=""
    p="$(trim_input "$(strip_wrapping_quotes "$p")")"
    [[ -n "$p" ]] || return 1
    [[ "$p" != *[[:space:]]* ]] || return 1
    [[ "$p" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*://[^[:space:]]+$ ]] || return 1
    scheme="${p%%://*}"
    case "${scheme,,}" in
        http|https|socks4|socks5|socks5h) ;;
        *) return 1 ;;
    esac
    printf '%s' "$p"
}

backend_is_valid() {
    local root="${1-}" version_file="${1-}/lib/otterrip_core/VERSION"
    local manifest="$root/lib/otterrip_core/REQUIRED_FILES" required found=false
    [[ -f "$root/lib/otterrip_core/__main__.py" &&
        -f "$root/lib/otterrip_core/REQUIRED_FILES" &&
        -f "$root/requirements.txt" &&
        -f "$version_file" ]] || return 1
    [[ "$(<"$version_file")" == "$OTTERRIP_VERSION" ]] || return 1
    while IFS= read -r required || [[ -n "$required" ]]; do
        [[ -n "$required" ]] || continue
        case "$required" in
            /*|*..*|*[!A-Za-z0-9._/-]*) return 1 ;;
        esac
        [[ -f "$root/lib/otterrip_core/$required" ]] || return 1
        found=true
    done < "$manifest"
    [[ "$found" == true ]]
}

set_backend_dir() {
    LIB_DIR="$1/lib"
    REQUIREMENTS_FILE="$1/requirements.txt"
}

# Comprueba que el paquete del backend no contenga rutas peligrosas, enlaces
# simbólicos/duros, miembros en exceso ni un tamaño descomprimido abusivo
# (protección frente a zip bombs).
# @brief Comprueba que el tar del backend no tenga rutas peligrosas, enlaces,
#        miembros en exceso ni un tamaño descomprimido abusivo.
# @param $1 Ruta del archivo .tar.gz a inspeccionar.
# @return 0 si el paquete es seguro; 1 si no lo es o no se puede leer.
# @side-effects Ninguno (solo lectura del archivo).
backend_archive_paths_are_safe() {
    local archive="${1-}"
    OR_MAX_SIZE_MB="$BACKEND_MAX_SIZE_MB" OR_MAX_MEMBERS="$BACKEND_MAX_MEMBERS" \
    python3 - "$archive" <<'PYEOF'
import os
import sys
import tarfile
from pathlib import PurePosixPath

max_size = int(os.environ.get("OR_MAX_SIZE_MB", "50")) * 1024 * 1024
max_members = int(os.environ.get("OR_MAX_MEMBERS", "500"))

archive_path = sys.argv[1]
try:
    with tarfile.open(archive_path, "r:gz") as archive:
        seen = set()
        total_size = 0
        count = 0
        for member in archive.getmembers():
            count += 1
            if count > max_members:
                raise ValueError(f"too many archive entries: {count}")
            name = member.name[2:] if member.name.startswith("./") else member.name
            path = PurePosixPath(name)
            if (
                not name
                or path.is_absolute()
                or "\\" in name
                or ".." in path.parts
                or name not in ("lib", "requirements.txt")
                and not name.startswith("lib/")
                or not (member.isdir() or member.isfile())
                or name in seen
            ):
                raise ValueError(f"unsafe archive entry: {member.name!r}")
            # Ni symlinks ni hardlinks: pueden apuntar fuera del directorio de
            # instalación al extraer el paquete.
            if member.issym() or member.islnk():
                raise ValueError(f"link in archive: {member.name!r}")
            total_size += max(int(member.size), 0)
            if total_size > max_size:
                raise ValueError(f"archive too large: {total_size} bytes")
            seen.add(name)
except (OSError, tarfile.TarError, ValueError, AttributeError):
    sys.exit(1)
PYEOF
}

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum -- "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 -- "$1" | awk '{print $1}'
    else
        echo "No se encontró sha256sum ni shasum para verificar el backend." >&2
        return 1
    fi
}

download_backend_archive() {
    local url="$BACKEND_ARCHIVE_URL" destination="${1-}"
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error --retry 2 \
            --connect-timeout 15 --max-time 120 --proto '=https' \
            --proto-redir '=https' "$url" --output "$destination"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only --timeout=20 --tries=3 --max-redirect=5 \
            --output-document="$destination" "$url"
    else
        echo "Se necesita curl o wget para descargar el backend de OtterRip." >&2
        return 1
    fi
}

# Recupera una instalación anterior del backend si la última actualización se
# interrumpió, y limpia los directorios temporales huérfanos.
# @brief Recupera el backend anterior tras una instalación interrumpida.
# @param $1 (no usado)
# @return 0 siempre (salvo error al restaurar, que devuelve 1).
# @side-effects Elimina directorios temporales de $APP_ROOT y restaura el backend.
recover_backend_install() {
    local tmp_dir="" backup="" owner="" basename=""
    [[ -d "$APP_ROOT" ]] || return 0
    local nullglob_was=false
    shopt -q nullglob && nullglob_was=true
    shopt -s nullglob
    for tmp_dir in "$APP_ROOT"/".v${OTTERRIP_VERSION}".[0-9]*.*; do
        [[ -d "$tmp_dir" ]] || continue
        basename="${tmp_dir##*/}"
        # Solo se tocan directorios con el nombre exacto que genera el script.
        [[ "$basename" =~ ^\.v[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\.[A-Za-z0-9]+$ ]] || continue
        if [[ "$basename" =~ ^\.v[0-9]+\.[0-9]+\.[0-9]+\.([0-9]+)\. ]]; then
            owner="${BASH_REMATCH[1]}"
            if [[ "$owner" != "$$" ]] && kill -0 "$owner" 2>/dev/null; then
                continue
            fi
        fi
        backup="$tmp_dir/previous-install"
        if backend_is_valid "$backup"; then
            if ! backend_is_valid "$APP_INSTALL_DIR"; then
                rm -rf -- "$APP_INSTALL_DIR"
                if ! mv -- "$backup" "$APP_INSTALL_DIR"; then
                    echo "No se pudo recuperar el backend anterior desde $backup." >&2
                    shopt -u nullglob
                    [[ "$nullglob_was" == "true" ]] && shopt -s nullglob
                    return 1
                fi
            fi
        fi
        rm -rf -- "$tmp_dir"
    done
    shopt -u nullglob
    [[ "$nullglob_was" == "true" ]] && shopt -s nullglob
    return 0
}

BACKEND_LOCK_DIR=""

# @brief Toma el bloqueo de instalación del backend (exclusión mutua).
# @param $1 (no usado)
# @return 0 si se obtuvo el bloqueo; 1 si se agotó el tiempo de espera.
# @side-effects Crea $APP_ROOT/.v<versión>.lock con el PID del proceso.
acquire_backend_lock() {
    local lock="$APP_ROOT/.v${OTTERRIP_VERSION}.lock" owner="" waited=0
    while ! mkdir -- "$lock" 2>/dev/null; do
        owner="$(cat -- "$lock/pid" 2>/dev/null || true)"
        if [[ "$owner" =~ ^[0-9]+$ ]] && ! kill -0 "$owner" 2>/dev/null; then
            rm -f -- "$lock/pid"
            rmdir -- "$lock" 2>/dev/null || true
            continue
        fi
        if [[ -z "$owner" ]] &&
            [[ -n "$(find "$lock" -maxdepth 0 -mmin +2 -print -quit 2>/dev/null)" ]]; then
            rmdir -- "$lock" 2>/dev/null || true
            continue
        fi
        if (( waited >= 120 )); then
            echo "Se agotó el tiempo esperando la instalación del backend ($lock)." >&2
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    if ! printf '%s\n' "$$" > "$lock/pid"; then
        rmdir -- "$lock" 2>/dev/null || true
        echo "No se pudo registrar el bloqueo de instalación del backend." >&2
        return 1
    fi
    BACKEND_LOCK_DIR="$lock"
}

# @brief Libera el bloqueo de instalación del backend.
# @param $1 (no usado)
# @return 0 siempre.
# @side-effects Elimina el directorio de bloqueo si lo posee este proceso.
release_backend_lock() {
    local owner=""
    [[ -n "$BACKEND_LOCK_DIR" ]] || return 0
    owner="$(cat -- "$BACKEND_LOCK_DIR/pid" 2>/dev/null || true)"
    if [[ "$owner" == "$$" ]]; then
        rm -f -- "$BACKEND_LOCK_DIR/pid"
        rmdir -- "$BACKEND_LOCK_DIR" 2>/dev/null || true
    fi
    BACKEND_LOCK_DIR=""
}

ensure_backend_unlocked() {
    local tmp_dir="" archive="" stage="" actual_hash="" backup=""
    recover_backend_install || return 1
    if backend_is_valid "$SCRIPT_DIR"; then
        set_backend_dir "$SCRIPT_DIR"
        return 0
    fi
    if backend_is_valid "$APP_INSTALL_DIR"; then
        set_backend_dir "$APP_INSTALL_DIR"
        return 0
    fi

    command -v python3 >/dev/null 2>&1 || {
        echo "Se necesita Python 3 para validar e instalar el backend." >&2
        return 1
    }
    if [[ ! "$BACKEND_SHA256" =~ ^[[:xdigit:]]{64}$ ]]; then
        echo "La versión $OTTERRIP_VERSION no tiene un SHA-256 de backend válido." >&2
        return 1
    fi
    if ! mkdir -p -- "$APP_ROOT"; then
        echo "No se pudo crear el directorio de instalación: $APP_ROOT" >&2
        return 1
    fi
    tmp_dir="$(mktemp -d "$APP_ROOT/.v${OTTERRIP_VERSION}.$$.XXXXXX")" || {
        echo "No se pudo crear un directorio temporal para instalar el backend." >&2
        return 1
    }
    archive="$tmp_dir/backend.tar.gz"
    stage="$tmp_dir/install"
    echo "Descargando el backend OtterRip $OTTERRIP_VERSION desde GitHub..."
    if ! download_backend_archive "$archive"; then
        echo "No se pudo descargar el backend. Revisa la conexión o conserva una instalación local válida." >&2
        rm -rf -- "$tmp_dir"
        return 1
    fi
    if ! actual_hash="$(sha256_file "$archive")" || [[ "$actual_hash" != "$BACKEND_SHA256" ]]; then
        echo "La verificación SHA-256 del backend falló; no se instalará el archivo." >&2
        rm -rf -- "$tmp_dir"
        return 1
    fi
    if ! backend_archive_paths_are_safe "$archive"; then
        echo "El paquete del backend contiene rutas inválidas o no se pudo inspeccionar." >&2
        rm -rf -- "$tmp_dir"
        return 1
    fi
    if ! mkdir -p -- "$stage" ||
        ! tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$stage" ||
        ! backend_is_valid "$stage"; then
        echo "El paquete descargado no contiene un backend OtterRip $OTTERRIP_VERSION válido." >&2
        rm -rf -- "$tmp_dir"
        return 1
    fi
    if ! python3 - "$stage/lib/otterrip_core" <<'PYEOF'
import ast
from pathlib import Path
import sys

for source in Path(sys.argv[1]).rglob("*.py"):
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
PYEOF
    then
        echo "La comprobación de sintaxis del backend descargado falló." >&2
        rm -rf -- "$tmp_dir"
        return 1
    fi

    if [[ -e "$APP_INSTALL_DIR" || -L "$APP_INSTALL_DIR" ]]; then
        backup="$tmp_dir/previous-install"
        if ! mv -- "$APP_INSTALL_DIR" "$backup"; then
            echo "No se pudo preservar la instalación anterior del backend." >&2
            rm -rf -- "$tmp_dir"
            return 1
        fi
    fi
    if ! mv -- "$stage" "$APP_INSTALL_DIR"; then
        if [[ -n "$backup" ]]; then
            if ! mv -- "$backup" "$APP_INSTALL_DIR"; then
                echo "No se pudo restaurar el backend anterior; se conserva en $backup." >&2
                return 1
            fi
        fi
        echo "No se pudo activar la instalación nueva del backend." >&2
        rm -rf -- "$tmp_dir"
        return 1
    fi
    if ! backend_is_valid "$APP_INSTALL_DIR"; then
        echo "La instalación del backend no superó la verificación final." >&2
        rm -rf -- "$APP_INSTALL_DIR"
        if [[ -n "$backup" ]] && ! mv -- "$backup" "$APP_INSTALL_DIR"; then
            echo "No se pudo restaurar el backend anterior; se conserva en $backup." >&2
            return 1
        fi
        rm -rf -- "$tmp_dir"
        return 1
    fi
    rm -rf -- "$tmp_dir"
    set_backend_dir "$APP_INSTALL_DIR"
    echo "Backend OtterRip $OTTERRIP_VERSION instalado en $APP_INSTALL_DIR."
}

# @brief Garantiza que hay un backend válido instalado (descarga si hace falta).
# @param $1 (no usado)
# @return 0 si el backend queda disponible; 1 si no se pudo instalar.
# @side-effects Puede descargar e instalar el backend en $APP_ROOT.
ensure_backend() {
    mkdir -p -- "$APP_ROOT" || {
        echo "No se pudo crear el directorio de instalación: $APP_ROOT" >&2
        return 1
    }
    acquire_backend_lock || return 1
    local rc=0
    ensure_backend_unlocked || rc=$?
    release_backend_lock
    return "$rc"
}

# ── Estado global ──────────────────────────────────────────────
AFMT="mp3"
AQ="320K"
PP_VALUE=""
PROFILE="MP3 320 kbps"
QUALITY_CHOICE=1
COVER_LOCAL=""
DUR_MIN=60
DUR_MAX=480
ALBUM_LAYOUT="single"
FETCH_LYRICS=true
LYRICS_MODE="embed"
LRCLIB_HOST="https://lrclib.net"
LYRICS_LANG=""
LYRICS_PROVIDERS="lrclib,multiweb"
LYRICS_PREFER_SYNCED=true
LYRICS_UPGRADE_PLAIN=false
LYRICS_SYNC_LEVEL="line"
LYRICS_SIDECAR=true
LYRICS_EMBED_STYLE="lrc"
LYRICS_MIN_CONFIDENCE=0
LYRICS_OFFSET_MS=0
ENV_PARALLEL_SET=false
[[ -n "${PARALLEL_JOBS:-}" ]] && ENV_PARALLEL_SET=true
PARALLEL_JOBS="${PARALLEL_JOBS:-3}"
LAST_DOWNLOAD_FOLDER="${LAST_DOWNLOAD_FOLDER:-Music}"
MAX_RETRY_PASSES=1

SYNCED_DIR="$SCRIPT_DIR/letras-webs-buscar"
SYNCEDLYRICS_AVAILABLE=false
YTDLP_BIN=""
PYBIN=""
JS_RUNTIME_ARGS=()
WORK_DIR=""
WORK_DONE=false
CHILD_PIDS=()
COMMON_FLAGS=()
DEST_LOCK_FD=""
QUEUE_TASK_ID=""
QUEUE_ACTIVE_TASK=""

# ── Estado de ejecución (flags de CLI) ─────────────────────────
DRY_RUN=false             # --dry-run: muestra el plan sin descargar nada
VERBOSITY=0               # 0 normal · 1 --verbose · 2 --debug
OTTERRIP_PROXY="${OTTERRIP_PROXY:-}"        # Proxy para yt-dlp (http/https/socks)
OTTERRIP_NOTIFY="${OTTERRIP_NOTIFY:-true}"  # Notificación de escritorio al terminar
OTTERRIP_CHECK_UPDATES="${OTTERRIP_CHECK_UPDATES:-true}"  # Aviso de versión nueva

# Plantilla de sesión: única fuente de verdad en Python (session.PRINT_TEMPLATE).
# Se duplica aquí para no depender de Python al sourcear el script; un test
# (tests/bats) comprueba que ambas coinciden.
SESSION_PRINT_TEMPLATE_DEFAULT='after_move:{"filepath":%(filepath|null)j,"title":%(title|null)j,"artist":%(artist|null)j,"uploader":%(uploader|null)j,"creator":%(creator|null)j,"album":%(album|null)j,"playlist_title":%(playlist_title|null)j,"release_year":%(release_year|null)j,"upload_date":%(upload_date|null)j,"genre":%(genre|null)j,"track_number":%(track_number|null)j,"disc_number":%(disc_number|null)j,"playlist_index":%(playlist_index|null)j,"id":%(id|null)j,"extractor_key":%(extractor_key|null)j,"duration":%(duration|null)j}'
SESSION_PRINT_TEMPLATE="$SESSION_PRINT_TEMPLATE_DEFAULT"

# Valida la plantilla de sesión: solo caracteres imprimibles y los tokens
# %(...)j esperados. Si está corrupta se restaura el valor por defecto.
# @brief Valida la plantilla de sesión usada por --print-to-file.
# @param $1 Plantilla a validar.
# @return 0 si es válida; 1 si está vacía, tiene controles o le faltan tokens.
# @side-effects Ninguno.
validate_session_template() {
    local t="${1-}" token=""
    [[ -n "$t" ]] || return 1
    # Sin caracteres de control (evita inyectar saltos de línea en el log).
    [[ "$t" =~ [[:cntrl:]] ]] && return 1
    for token in "%(filepath|null)j" "%(title|null)j" "%(artist|null)j" "%(id|null)j"; do
        [[ "$t" == *"$token"* ]] || return 1
    done
    return 0
}

# ── Colores (según TTY) ────────────────────────────────────────
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
    CYAN=$'\033[0;36m'; MAGENTA=$'\033[0;35m'
    BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; CYAN=''; MAGENTA=''
    BOLD=''; DIM=''; NC=''
fi

# ═══════════════════════════════════════════════════════════════
#  REGISTRO (log con rotación) Y ACCESO AL NÚCLEO PYTHON
# ═══════════════════════════════════════════════════════════════
log_event() {
    local level="${1-INFO}" msg="${2-}" size=0
    [[ -d "$OTTERRIP_STATE_DIR" ]] || return 0
    if [[ -f "$LOG_FILE" ]]; then
        size="$(wc -c < "$LOG_FILE" 2>/dev/null || printf '0')"
        size="${size//[[:space:]]/}"
        if [[ "$size" =~ ^[0-9]+$ ]] && (( size > MAX_LOG_SIZE )); then
            mv -f -- "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
        fi
    fi
    printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# Ejecuta el paquete Python con el intérprete elegido (venv o sistema).
py_core() {
    PYTHONPATH="$LIB_DIR${PYTHONPATH:+:$PYTHONPATH}" PYTHONDONTWRITEBYTECODE=1 \
        "${PYBIN:-python3}" -m otterrip_core "$@"
}

# ═══════════════════════════════════════════════════════════════
#  LIMPIEZA Y SEÑALES
# ═══════════════════════════════════════════════════════════════
kill_tree() {
    local pid="${1-}" sig="${2:-TERM}" child
    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    if command -v pgrep >/dev/null 2>&1; then
        while IFS= read -r child; do
            [[ -n "$child" ]] && kill_tree "$child" "$sig"
        done < <(pgrep -P "$pid" 2>/dev/null || true)
    fi
    kill -s "$sig" -- "-$pid" 2>/dev/null || true
    kill -s "$sig" "$pid" 2>/dev/null || true
}

cleanup() {
    local rc=$? pid
    trap - EXIT INT TERM HUP
    if ((${#CHILD_PIDS[@]} > 0)); then
        for pid in "${CHILD_PIDS[@]}"; do
            kill_tree "$pid" TERM
        done
        sleep 0.4
        for pid in "${CHILD_PIDS[@]}"; do
            kill_tree "$pid" KILL
        done
        CHILD_PIDS=()
    fi
    # Una tarea de cola interrumpida queda PAUSADA (reanudable), no perdida.
    if [[ -n "$QUEUE_ACTIVE_TASK" ]]; then
        py_core queue finish "$QUEUE_ACTIVE_TASK" paused \
            --error "interrumpida; reanuda desde el menú de cola" >/dev/null 2>&1 || true
        QUEUE_ACTIVE_TASK=""
    fi
    if [[ -n "${WORK_DIR:-}" && -d "${WORK_DIR:-}" ]]; then
        # Con --debug no se borra nada: los temporales se conservan para analizar.
        if (( VERBOSITY >= 2 )); then
            printf '\n  Depuración: sesión conservada en %s\n' "$WORK_DIR" >&2
            WORK_DIR=""
        elif [[ "$WORK_DONE" == "true" ]] || ! session_dir_has_entries "$WORK_DIR"; then
            rm -rf -- "$WORK_DIR" 2>/dev/null || true
        else
            printf '\n  Sesión conservada para recuperación: %s\n' "$WORK_DIR" >&2
            log_event WARN "sesión conservada tras interrupción: $WORK_DIR"
        fi
        WORK_DIR=""
    fi
    release_backend_lock
    release_dest_lock
    exit "$rc"
}

trap cleanup EXIT
trap 'printf "\n  Interrumpido. Limpiando...\n"; exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# ═══════════════════════════════════════════════════════════════
#  HELPERS GENERALES
# ═══════════════════════════════════════════════════════════════
clear_screen() { clear 2>/dev/null || true; }

pause_enter() { read -rp "  Enter para continuar..." _ || true; }

is_positive_int() {
    local value="${1-}"
    [[ "$value" =~ ^[0-9]+$ ]] || return 1
    while [[ ${#value} -gt 1 && "${value:0:1}" == "0" ]]; do
        value="${value:1}"
    done
    [[ "$value" != "0" ]]
}

# Entero no negativo (0 incluido). Para contadores y cálculos aritméticos.
is_nonneg_int() {
    local value="${1-}"
    [[ "$value" =~ ^[0-9]+$ ]]
}

normalize_jobs() {
    local v="${1:-3}"
    is_positive_int "$v" || v=3
    while [[ ${#v} -gt 1 && "${v:0:1}" == "0" ]]; do
        v="${v:1}"
    done
    if [[ ${#v} -gt 2 ]]; then
        v=$MAX_WORKERS
    else
        v=$((10#$v))
        (( v > MAX_WORKERS )) && v=$MAX_WORKERS
    fi
    printf '%s' "$v"
}
PARALLEL_JOBS="$(normalize_jobs "$PARALLEL_JOBS")"

trim_input() {
    local s="${1-}"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

strip_wrapping_quotes() {
    local s="${1-}"
    if (( ${#s} >= 2 )); then
        if [[ "$s" == \"*\" || "$s" == \'*\' ]]; then
            s="${s:1:${#s}-2}"
        fi
    fi
    printf '%s' "$s"
}

expand_tilde() {
    local p="${1-}"
    case "$p" in
        \~) p="$HOME" ;;
        \~/*) p="$HOME/${p#\~/}" ;;
    esac
    printf '%s' "$p"
}

# Sanea un ÚNICO componente de ruta. Conserva espacios, acentos y Unicode.
safe_name() {
    local s="${1-}" blen
    s="${s//$'\r'/ }"
    s="${s//$'\n'/ }"
    s="${s//$'\t'/ }"
    s="$(printf '%s' "$s" | LC_ALL=C tr -d '\000-\037')"
    s="${s//\//_}"
    s="${s//\\/_}"
    s="${s//</_}"
    s="${s//>/_}"
    s="${s//:/_}"
    s="${s//\"/_}"
    s="${s//|/_}"
    s="${s//\?/_}"
    s="${s//\*/_}"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    while [[ "$s" == .* ]]; do s="${s#.}"; done
    [[ -z "$s" ]] && s="_"
    blen="$(printf '%s' "$s" | LC_ALL=C wc -c)"
    blen="${blen//[[:space:]]/}"
    if [[ "$blen" =~ ^[0-9]+$ ]] && (( blen > 200 )); then
        s="$("${PYBIN:-python3}" -c 'import sys; b=sys.argv[1].encode("utf-8")[:200]; sys.stdout.write(b.decode("utf-8","ignore"))' "$s" 2>/dev/null || printf '%s' "${s:0:80}")"
    fi
    [[ -z "$s" ]] && s="_"
    printf '%s' "$s"
}

# Resuelve una ruta absoluta o relativa a $DOWNLOADS. No usa eval.
resolve_destination() {
    local input="${1-}" comp rel="" resolved
    input="$(trim_input "$(strip_wrapping_quotes "$input")")"
    input="$(expand_tilde "$input")"
    if [[ -z "$input" ]]; then
        input="Music"
    fi
    if [[ "$input" == /* ]]; then
        resolved="$(resolve_path "$input")"
        printf '%s' "$resolved"
        return 0
    fi
    local IFS='/'
    local -a parts=()
    read -r -a parts <<< "$input" || true
    for comp in "${parts[@]}"; do
        [[ -z "$comp" || "$comp" == "." ]] && continue
        if [[ "$comp" == ".." ]]; then
            printf '%s' ""
            return 1
        fi
        comp="$(safe_name "$comp")"
        rel="${rel:+$rel/}$comp"
    done
    [[ -z "$rel" ]] && rel="Music"
    resolved="$(resolve_path "$DOWNLOADS/$rel")"
    printf '%s' "$resolved"
}

destination_display() {
    local dest
    if dest="$(resolve_destination "$LAST_DOWNLOAD_FOLDER")"; then
        printf '%s' "$dest"
    else
        printf '%s' "$DOWNLOADS/Music"
    fi
}

format_seconds() {
    local s="${1:-0}"
    [[ "$s" =~ ^[0-9]+$ ]] || s=0
    printf '%dm %02ds' $((s / 60)) $((s % 60))
}

count_lines() {
    local f="${1-}" n=0
    if [[ -n "$f" && -f "$f" ]]; then
        n="$(awk 'NF { c++ } END { print c+0 }' "$f" 2>/dev/null || printf '0')"
        n="${n//[[:space:]]/}"
        [[ "$n" =~ ^[0-9]+$ ]] || n=0
    fi
    printf '%s' "$n"
}

summary_value() {
    local f="${1-}" key="${2-}" def="${3:-0}" val=""
    if [[ -f "$f" ]]; then
        val="$(awk -F= -v k="$key" '$1==k { print $2; exit }' "$f" 2>/dev/null || true)"
    fi
    val="${val//[[:space:]]/}"
    [[ "$val" =~ ^[0-9]+$ ]] || val="$def"
    printf '%s' "$val"
}

normalize_host() {
    local h="${1-}"
    h="$(trim_input "$(strip_wrapping_quotes "$(trim_input "$h")")")"
    h="${h%/}"
    [[ "$h" =~ ^https?://[^[:space:]]+$ ]] || return 1
    printf '%s' "$h"
}

is_http_url() {
    [[ "${1-}" =~ ^https?://[^[:space:]]+$ ]]
}

# Pregunta [s/N]. Con OTTERRIP_ASSUME_YES=1 acepta; sin TTY rechaza.
confirm() {
    local prompt="${1-}" ans=""
    [[ "${OTTERRIP_ASSUME_YES:-0}" == "1" ]] && return 0
    [[ -t 0 ]] || return 1
    read -rp "  $prompt [s/N]: " ans || return 1
    case "${ans,,}" in
        s|si|sí|y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# Pide una ruta existente. Resultado en ASKED_PATH (vacío = cancelar).
ASKED_PATH=""
ask_path() {
    local prompt="${1-}" default="${2-}" raw=""
    ASKED_PATH=""
    read -rp "  $prompt${default:+ (Enter=$default)}: " raw || return 1
    raw="$(trim_input "$(strip_wrapping_quotes "$raw")")"
    raw="$(expand_tilde "$raw")"
    [[ -z "$raw" ]] && raw="$default"
    [[ -z "$raw" ]] && return 1
    if [[ ! -e "$raw" ]]; then
        msg_err "No existe: $raw"
        return 1
    fi
    ASKED_PATH="$(resolve_path "$raw")"
    return 0
}

# ═══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN PERSISTENTE (sin `source`: solo claves validadas)
# ═══════════════════════════════════════════════════════════════
apply_quality_choice() {
    case "${1-}" in
        1) AFMT="mp3";  AQ="320K"; PP_VALUE=""; PROFILE="MP3 320 kbps" ;;
        2) AFMT="m4a";  AQ="256K"; PP_VALUE=""; PROFILE="M4A AAC 256 kbps" ;;
        3) AFMT="m4a";  AQ="320K"; PP_VALUE=""; PROFILE="M4A AAC 320 kbps" ;;
        4) AFMT="flac"; AQ="0";    PP_VALUE="ffmpeg:-sample_fmt s16 -ar 44100"; PROFILE="FLAC 16-bit/44.1kHz" ;;
        5) AFMT="flac"; AQ="0";    PP_VALUE="ffmpeg:-sample_fmt s32 -ar 96000"; PROFILE="FLAC 24-bit/96kHz" ;;
        *) return 1 ;;
    esac
    # shellcheck disable=SC2034  # se persiste vía save_config (expansión indirecta)
    QUALITY_CHOICE="$1"
    return 0
}

is_bool() { [[ "${1-}" == "true" || "${1-}" == "false" ]]; }

# ── Esquema de configuración ───────────────────────────────────
# Formato de cada entrada: "tipo:validación:valor_por_defecto"
#   int      → entero positivo (máx. 9 dígitos)
#   bool     → true | false
#   enum     → uno de los valores separados por comas
#   host     → URL http(s) válida
#   lang     → código de idioma de 2 letras (o vacío)
#   providers→ lrclib | multiweb | lrclib,multiweb
#   browser  → nombre de navegador seguro
#   path     → ruta existente (o vacío)
#   folder   → carpeta de destino (sin tabuladores)
#   proxy    → URL de proxy válida (o vacío)
#   string   → texto sin saltos de línea ni tabuladores
declare -gA CONFIG_SCHEMA=(
    [QUALITY_CHOICE]="enum:1,2,3,4,5:1"
    [DUR_MIN]="int:-:60"
    [DUR_MAX]="int:-:480"
    [PARALLEL_JOBS]="int:-:3"
    [ALBUM_LAYOUT]="enum:single,split:single"
    [FETCH_LYRICS]="bool:-:true"
    [LYRICS_MODE]="enum:embed,file:embed"
    [LRCLIB_HOST]="host:-:https://lrclib.net"
    [LYRICS_LANG]="lang:-:"
    [LYRICS_PROVIDERS]="providers:-:lrclib,multiweb"
    [LYRICS_PREFER_SYNCED]="bool:-:true"
    [LYRICS_UPGRADE_PLAIN]="bool:-:false"
    [LYRICS_SYNC_LEVEL]="enum:line,word,auto:line"
    [LYRICS_SIDECAR]="bool:-:true"
    [LYRICS_EMBED_STYLE]="enum:lrc,plain:lrc"
    [LYRICS_MIN_CONFIDENCE]="int:-:0"
    [LYRICS_OFFSET_MS]="int:-:0"
    [LAST_DOWNLOAD_FOLDER]="folder:-:Music"
    [COVER_LOCAL]="path:-:"
    [COOKIES_BROWSER]="browser:-:"
    [COOKIES_FILE]="path:-:"
    [OTTERRIP_PROXY]="proxy:-:"
    [OTTERRIP_NOTIFY]="bool:-:true"
    [OTTERRIP_CHECK_UPDATES]="bool:-:true"
)

# Orden estable de las claves al guardar el archivo de configuración.
CONFIG_KEYS=(
    QUALITY_CHOICE DUR_MIN DUR_MAX PARALLEL_JOBS ALBUM_LAYOUT FETCH_LYRICS
    LYRICS_MODE LRCLIB_HOST LYRICS_LANG LYRICS_PROVIDERS LYRICS_PREFER_SYNCED
    LYRICS_UPGRADE_PLAIN LYRICS_SYNC_LEVEL LYRICS_SIDECAR LYRICS_EMBED_STYLE
    LYRICS_MIN_CONFIDENCE LYRICS_OFFSET_MS LAST_DOWNLOAD_FOLDER COVER_LOCAL COOKIES_BROWSER
    COOKIES_FILE OTTERRIP_PROXY OTTERRIP_NOTIFY OTTERRIP_CHECK_UPDATES
)

# Valida un valor contra el esquema. Devuelve 0 si el valor es aceptable.
# @brief Valida un valor de configuración contra el esquema.
# @param $1 Tipo (int|bool|enum|host|lang|providers|browser|path|folder|proxy|string).
# @param $2 Especificación del tipo (lista de valores para enum).
# @param $3 Valor a validar.
# @return 0 si el valor es válido; 1 si no lo es.
# @side-effects Ninguno.
config_validate_value() {
    local type="${1-}" spec="${2-}" value="${3-}"
    case "$type" in
        int)
            [[ ${#value} -le 9 ]] || return 1
            is_positive_int "$value" || return 1
            ;;
        bool)
            is_bool "$value" || return 1
            ;;
        enum)
            [[ ",$spec," == *",$value,"* ]] || return 1
            ;;
        host)
            normalize_host "$value" >/dev/null || return 1
            ;;
        lang)
            [[ "$value" =~ ^([a-z]{2})?$ ]] || return 1
            ;;
        providers)
            case "$value" in lrclib|multiweb|lrclib,multiweb) ;; *) return 1 ;; esac
            ;;
        browser)
            [[ "$value" =~ ^[a-zA-Z0-9_.:+-]*$ ]] || return 1
            ;;
        path)
            [[ -z "$value" || -f "$value" ]] || return 1
            ;;
        folder)
            [[ -n "$value" && "$value" != *$'\t'* && "$value" != *$'\n'* ]] || return 1
            ;;
        proxy)
            if [[ -n "$value" ]]; then
                normalize_proxy "$value" >/dev/null || return 1
            fi
            ;;
        string)
            [[ "$value" != *$'\t'* && "$value" != *$'\n'* ]] || return 1
            ;;
        *)
            return 1
            ;;
    esac
    return 0
}

# Aplica un valor validado a su variable global.
config_apply_value() {
    local key="${1-}" value="${2-}"
    case "$key" in
        QUALITY_CHOICE)
            apply_quality_choice "$value" || true
            ;;
        PARALLEL_JOBS)
            if [[ "$ENV_PARALLEL_SET" != "true" ]]; then
                PARALLEL_JOBS="$(normalize_jobs "$value")"
            fi
            ;;
        DUR_MIN|DUR_MAX)
            printf -v "$key" '%s' "$((10#$value))"
            ;;
        LRCLIB_HOST)
            LRCLIB_HOST="$(normalize_host "$value")"
            ;;
        OTTERRIP_PROXY)
            if [[ -n "$value" ]]; then
                OTTERRIP_PROXY="$(normalize_proxy "$value")"
            fi
            ;;
        *)
            printf -v "$key" '%s' "$value"
            ;;
    esac
    return 0
}

# @brief Carga la configuración persistente validando cada clave.
# @param $1 (no usado; lee $CONFIG_FILE)
# @return 0 siempre (las claves inválidas se ignoran).
# @side-effects Sobrescribe variables globales con los valores guardados.
load_config() {
    [[ -f "$CONFIG_FILE" ]] || return 0
    local key="" value="" entry="" type="" spec="" def="" rest=""
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        value="${value%$'\r'}"
        entry="${CONFIG_SCHEMA[$key]:-}"
        [[ -n "$entry" ]] || continue
        type="${entry%%:*}"
        rest="${entry#*:}"
        spec="${rest%%:*}"
        def="${rest#*:}"
        if ! config_validate_value "$type" "$spec" "$value"; then
            # Valor inválido: se recurre al valor por defecto del esquema.
            value="$def"
            [[ -n "$value" ]] || continue
        fi
        config_apply_value "$key" "$value"
    done < "$CONFIG_FILE"
    return 0
}

# @brief Guarda la configuración persistente de forma atómica.
# @param $1 (no usado)
# @return 0 siempre (los fallos no son fatales).
# @side-effects Escribe y renombra $CONFIG_FILE.
save_config() {
    local tmp="" key="" value=""
    tmp="$(secure_mktemp "$OTTERRIP_CONFIG_DIR" "config")" || return 0
    {
        echo "# OtterRip — generado automáticamente. Puedes editarlo; los valores inválidos se ignoran."
        for key in "${CONFIG_KEYS[@]}"; do
            value="${!key:-}"
            # Nunca escribir saltos de línea ni tabuladores: romperían el formato.
            value="${value//$'\n'/ }"
            value="${value//$'\t'/ }"
            printf '%s=%s\n' "$key" "$value"
        done
    } > "$tmp" 2>/dev/null || { rm -f -- "$tmp"; return 0; }
    chmod 600 -- "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$CONFIG_FILE" 2>/dev/null || rm -f -- "$tmp"
    return 0
}

# ═══════════════════════════════════════════════════════════════
#  DEPENDENCIAS (instalación aislada y con confirmación)
# ═══════════════════════════════════════════════════════════════
detect_pkg_manager() {
    local pm
    for pm in pacman apt-get dnf zypper apk; do
        if command -v "$pm" >/dev/null 2>&1; then
            printf '%s' "$pm"
            return 0
        fi
    done
    return 1
}

# Nombre de paquete según gestor (los nombres difieren entre distribuciones).
pkg_names_for() {
    local pm="${1-}" logical="${2-}"
    case "$pm:$logical" in
        apt-get:python-venv) printf 'python3-venv' ;;
        pacman:python-venv|zypper:python-venv|dnf:python-venv) printf 'python3' ;;
        pacman:python3) printf 'python' ;;
        apk:python-venv) printf 'python3 py3-pip' ;;
        *:python3) printf 'python3' ;;
        *:ffmpeg) printf 'ffmpeg' ;;
        *:shellcheck) printf 'shellcheck' ;;
        *:bats) printf 'bats' ;;
        *) printf '%s' "$logical" ;;
    esac
}

# Instala paquetes del sistema. El llamador debe haber pedido confirmación.
pkg_install() {
    (($#)) || return 0
    local pm=""
    pm="$(detect_pkg_manager)" || {
        echo "  ${YELLOW}⚠${NC} No se detectó un gestor de paquetes compatible."
        return 1
    }
    local -a priv=()
    if (( EUID != 0 )); then
        if command -v sudo >/dev/null 2>&1; then
            priv=(sudo)
        elif command -v doas >/dev/null 2>&1; then
            priv=(doas)
        else
            echo "  ${YELLOW}⚠${NC} Se necesita sudo/doas (o ser root) para instalar paquetes."
            return 1
        fi
    fi
    log_event INFO "pkg_install via $pm: $*"
    case "$pm" in
        pacman) "${priv[@]}" pacman -S --needed --noconfirm "$@" ;;
        apt-get)
            "${priv[@]}" apt-get install -y "$@" 2>/dev/null \
                || { "${priv[@]}" apt-get update -qq && "${priv[@]}" apt-get install -y "$@"; } ;;
        dnf) "${priv[@]}" dnf install -y "$@" ;;
        zypper) "${priv[@]}" zypper --non-interactive install "$@" ;;
        apk) "${priv[@]}" apk add --no-cache "$@" ;;
        *) return 1 ;;
    esac
}

install_hint_for() {
    local pm="" pkg=""
    pm="$(detect_pkg_manager 2>/dev/null || true)"
    pkg="$(pkg_names_for "$pm" "${1-}")"
    case "$pm" in
        pacman) printf 'sudo pacman -S --needed %s' "$pkg" ;;
        apt-get) printf 'sudo apt install %s' "$pkg" ;;
        dnf) printf 'sudo dnf install %s' "$pkg" ;;
        zypper) printf 'sudo zypper install %s' "$pkg" ;;
        apk) printf 'doas apk add %s   (o sudo)' "$pkg" ;;
        *) printf "instala '%s' con el gestor de paquetes de tu distribución" "${1-}" ;;
    esac
}

python_version_ok() {
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

libs_ok_for() {
    "${1-python3}" -c 'import aiohttp, mutagen, requests; from syncedlyrics import search; from importlib.metadata import version; v=tuple(map(int, version("syncedlyrics").split(".")[:3])); assert (1, 0, 1) <= v < (2, 0, 0)' >/dev/null 2>&1
}

# Instala paquetes Python SOLO dentro del venv de OtterRip.
venv_pip_install() {
    (($#)) || return 0
    local vpy="$VENV_DIR/bin/python" logfile=""
    [[ -x "$vpy" ]] || { echo "  ${RED}✘${NC} El entorno virtual no existe."; return 1; }
    validate_binary_path "$vpy" || {
        echo "  ${RED}✘${NC} El intérprete del venv no está en una ruta segura: $vpy"
        return 1
    }
    # El log vive en el directorio de estado (modo 700), no en /tmp: evita
    # ataques por symlink y la lectura por otros usuarios.
    logfile="$(secure_mktemp "$OTTERRIP_STATE_DIR" "otterrip-pip.log")" || logfile=""
    if "$vpy" -m pip install --disable-pip-version-check --upgrade "$@" >"${logfile:-/dev/null}" 2>&1; then
        [[ -n "$logfile" ]] && rm -f -- "$logfile"
        log_event INFO "pip (venv) instaló: $*"
        return 0
    fi
    echo "  ${RED}✘${NC} Falló la instalación de: $*"
    if [[ -n "$logfile" ]]; then
        tail -n 6 -- "$logfile" 2>/dev/null | sed 's/^/    /' || true
        rm -f -- "$logfile"
    fi
    log_event ERROR "pip (venv) falló: $*"
    return 1
}

create_venv() {
    if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
        echo "  ${YELLOW}⚠${NC} Falta el módulo venv/ensurepip de Python."
        local pkg=""
        pkg="$(pkg_names_for "$(detect_pkg_manager 2>/dev/null || true)" python-venv)"
        if confirm "¿Instalar '$pkg' ahora (requiere permisos de administrador)?"; then
            # shellcheck disable=SC2086  # puede ser más de un paquete
            pkg_install $pkg || true
        fi
        if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
            echo "  ${RED}✘${NC} No se puede crear el entorno virtual."
            echo "    Instálalo con: $(install_hint_for python-venv)"
            return 1
        fi
    fi
    # Solo se recrea un venv propio (dentro del directorio de datos de OtterRip).
    if [[ -d "$VENV_DIR" && "$VENV_DIR" == "$OTTERRIP_DATA_DIR/venv" ]]; then
        rm -rf -- "$VENV_DIR"
    fi
    echo "  ${YELLOW}⚙${NC} Creando entorno virtual aislado en $VENV_DIR ..."
    if ! python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
        [[ "$VENV_DIR" == "$OTTERRIP_DATA_DIR/venv" ]] && rm -rf -- "$VENV_DIR"
        echo "  ${RED}✘${NC} Falló la creación del entorno virtual."
        return 1
    fi
    return 0
}

# Elige el intérprete: venv propio (preferido) o Python del sistema si ya trae
# las librerías (nunca se modifica el Python del sistema).
ensure_python_env() {
    local vpy="$VENV_DIR/bin/python" need_ytdlp=false
    [[ -z "$(find_ytdlp)" ]] && need_ytdlp=true

    if [[ -x "$vpy" ]] && "$vpy" -c 'import sys' >/dev/null 2>&1; then
        PYBIN="$vpy"
    elif libs_ok_for python3 && [[ "$need_ytdlp" == "false" ]]; then
        PYBIN="$(command -v python3)"
    else
        create_venv || return 1
        PYBIN="$vpy"
    fi

    # El intérprete debe ser una ruta absoluta y segura (nada de symlinks a
    # /tmp o directorios escribibles por cualquiera).
    if ! validate_binary_path "$PYBIN"; then
        echo "  ${RED}✘${NC} El intérprete de Python no es una ruta segura: $PYBIN"
        echo "    Se aborta para evitar ejecutar un binario manipulado."
        return 1
    fi

    if ! libs_ok_for "$PYBIN"; then
        echo "  ${YELLOW}⚙${NC} Instalando dependencias Python de OtterRip en el venv..."
        venv_pip_install -r "$REQUIREMENTS_FILE" || {
            echo "    Verifica la conexión y que pip pueda instalar syncedlyrics>=1.0.1,<2."
            echo "    Multi-Web necesita esa API; LRCLIB por sí solo no sustituye el fallback configurado."
            return 1
        }
    fi
    return 0
}

find_ytdlp() {
    if [[ -n "${OTTERRIP_YTDLP:-}" && -x "${OTTERRIP_YTDLP}" ]]; then
        printf '%s' "$OTTERRIP_YTDLP"
    elif [[ -x "$VENV_DIR/bin/yt-dlp" ]]; then
        printf '%s' "$VENV_DIR/bin/yt-dlp"
    elif command -v yt-dlp >/dev/null 2>&1; then
        command -v yt-dlp
    elif [[ -x "$HOME/.local/bin/yt-dlp" ]]; then
        printf '%s' "$HOME/.local/bin/yt-dlp"
    fi
}

ensure_ytdlp() {
    YTDLP_BIN="$(find_ytdlp)"
    if [[ -z "$YTDLP_BIN" ]]; then
        echo "  ${YELLOW}⚙${NC} Instalando yt-dlp (con yt-dlp-ejs) en el venv..."
        if [[ "$PYBIN" != "$VENV_DIR/bin/python" ]]; then
            create_venv && PYBIN="$VENV_DIR/bin/python" || return 1
            libs_ok_for "$PYBIN" || venv_pip_install -r "$REQUIREMENTS_FILE" || return 1
        fi
        venv_pip_install "yt-dlp[default]" || venv_pip_install yt-dlp || {
            echo "    Alternativa: paquete de tu distribución (pacman -S yt-dlp, apt install yt-dlp...)"
            return 1
        }
        YTDLP_BIN="$(find_ytdlp)"
    fi
    # El binario de yt-dlp debe ser una ruta absoluta y segura.
    if [[ -n "$YTDLP_BIN" ]] && ! validate_binary_path "$YTDLP_BIN"; then
        echo "  ${RED}✘${NC} yt-dlp no está en una ruta segura: $YTDLP_BIN"
        echo "    Se aborta para evitar ejecutar un binario manipulado."
        return 1
    fi
    export YTDLP_BIN
    local ytv=""
    ytv="$(ytdlp --version 2>/dev/null | head -n1 || true)"
    if [[ -z "$ytv" ]]; then
        echo "  ${RED}✘${NC} yt-dlp no responde ($YTDLP_BIN)."
        echo "    Si es un binario antiguo, actualízalo o reinstálalo desde el menú de diagnóstico."
        return 1
    fi
    echo "  ${GREEN}✔${NC} yt-dlp $ytv"
    return 0
}

# Ejecuta yt-dlp. Con --verbose muestra el comando completo antes de lanzarlo.
ytdlp() {
    if (( VERBOSITY >= 1 )); then
        printf '[yt-dlp] %s\n' "$(printf '%q ' "$@")" >&2
    fi
    if [[ -n "${YTDLP_BIN:-}" ]]; then
        "$YTDLP_BIN" "$@"
    else
        "${PYBIN:-python3}" -m yt_dlp "$@"
    fi
}

ytdlp_has_flag() {
    ytdlp --help 2>/dev/null | grep -q -- "${1-}"
}

# YouTube necesita un runtime JS externo. yt-dlp solo habilita deno por
# defecto; para node/bun/quickjs hace falta --js-runtimes (si la versión lo admite).
setup_js_runtime() {
    JS_RUNTIME_ARGS=()
    if command -v deno >/dev/null 2>&1; then
        return 0
    fi
    local rt name
    for rt in node bun qjs; do
        if command -v "$rt" >/dev/null 2>&1; then
            name="$rt"
            [[ "$rt" == "qjs" ]] && name="quickjs"
            if ytdlp_has_flag '--js-runtimes'; then
                JS_RUNTIME_ARGS=(--js-runtimes "$name")
                echo "  ${GREEN}✔${NC} Runtime JS: $rt (para YouTube)"
            else
                echo "  ${YELLOW}!${NC} yt-dlp no admite --js-runtimes; actualiza yt-dlp para usar $rt."
            fi
            return 0
        fi
    done
    echo "  ${YELLOW}!${NC} Sin runtime JS (deno/node/bun/quickjs): algunas descargas de YouTube pueden fallar."
    return 0
}

refresh_syncedlyrics_state() {
    if "${PYBIN:-python3}" - "$SYNCED_DIR" >/dev/null 2>&1 <<'PYEOF'
import os, sys
p = sys.argv[1]
if p and os.path.isdir(p):
    sys.path.insert(0, p)
try:
    import syncedlyrics  # noqa: F401
except Exception:
    sys.exit(1)
PYEOF
    then
        SYNCEDLYRICS_AVAILABLE=true
    else
        SYNCEDLYRICS_AVAILABLE=false
    fi
}

provider_label() {
    local base="LRCLIB"
    case "$LYRICS_PROVIDERS" in
        lrclib) base="LRCLIB" ;;
        multiweb) base="Multi-Web" ;;
        *) base="LRCLIB + Multi-Web" ;;
    esac
    if [[ "$SYNCEDLYRICS_AVAILABLE" != "true" && "$LYRICS_PROVIDERS" != "lrclib" ]]; then
        base="$base (syncedlyrics no instalado)"
    fi
    printf '%s' "$base"
}

# Momento de modificación de un archivo (epoch). Compatible con GNU y BSD.
file_mtime() {
    local f="${1-}" t=""
    [[ -f "$f" ]] || { printf '0'; return 0; }
    t="$(stat -c '%Y' -- "$f" 2>/dev/null || true)"
    if [[ -z "$t" ]]; then
        t="$(stat -f '%m' -- "$f" 2>/dev/null || true)"
    fi
    t="${t//[[:space:]]/}"
    [[ "$t" =~ ^[0-9]+$ ]] || t=0
    printf '%s' "$t"
}

# Claves admitidas en la caché de dependencias (lista blanca estricta).
DEPS_CACHE_KEYS=(
    CACHED_OTTERRIP_VERSION
    CACHED_LIB_DIR
    CACHED_REQUIREMENTS_FILE
    CACHED_PYBIN
    CACHED_YTDLP_BIN
    CACHED_SYNCEDLYRICS_AVAILABLE
    CACHED_JS_ARGS
    CACHED_TIMESTAMP
)

# Indica si una clave pertenece a la lista blanca de la caché.
deps_cache_key_allowed() {
    case "${1-}" in
        CACHED_OTTERRIP_VERSION|CACHED_LIB_DIR|CACHED_REQUIREMENTS_FILE|\
        CACHED_PYBIN|CACHED_YTDLP_BIN|CACHED_SYNCEDLYRICS_AVAILABLE|\
        CACHED_JS_ARGS|CACHED_TIMESTAMP) return 0 ;;
    esac
    return 1
}

# Lee la caché de dependencias SIN `source` ni `eval`: solo se aceptan líneas
# `CLAVE=valor` donde CLAVE pertenece a la lista blanca. Cualquier otra línea
# invalida la caché completa.
# @brief Lee la caché de dependencias sin ejecutar su contenido.
# @param $1 (no usado; lee $DEPS_CACHE_FILE)
# @return 0 si el archivo es válido; 1 si contiene una línea no permitida.
# @side-effects Define las variables CACHED_* mediante printf -v (lista blanca).
load_deps_cache() {
    local line="" key="" value=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "$line" ]] && continue
        [[ "$line" == \#* ]] && continue
        # Sin caracteres de control: impide valores con saltos de línea.
        [[ "$line" =~ [[:cntrl:]] ]] && return 1
        [[ "$line" == *=* ]] || return 1
        key="${line%%=*}"
        value="${line#*=}"
        [[ "$key" =~ ^[A-Z_]+$ ]] || return 1
        deps_cache_key_allowed "$key" || return 1
        printf -v "$key" '%s' "$value"
    done < "$DEPS_CACHE_FILE"
    return 0
}

# Comprueba si la caché de dependencias es válida (menos de 24 h, ejecutables
# presentes y versión coincidente). Devuelve 0 si se puede saltar la verificación.
# @brief Indica si la caché de dependencias es válida y utilizable.
# @param $1 (no usado)
# @return 0 si la caché es válida; 1 si hay que volver a verificar.
# @side-effects Define las variables CACHED_* (globales) al leer la caché.
deps_cache_valid() {
    [[ -f "$DEPS_CACHE_FILE" ]] || return 1
    local now cache_time
    now="$(date +%s)"
    cache_time="$(file_mtime "$DEPS_CACHE_FILE")"
    (( now - cache_time < DEPS_CACHE_TTL )) || return 1
    # Lectura segura: nunca se ejecuta el contenido del archivo.
    load_deps_cache || return 1
    [[ "${CACHED_OTTERRIP_VERSION:-}" == "$OTTERRIP_VERSION" ]] || return 1
    # El backend debe seguir siendo válido en disco; si se borró, invalida caché.
    [[ -n "${CACHED_LIB_DIR:-}" ]] || return 1
    backend_is_valid "$(dirname -- "$CACHED_LIB_DIR")" || return 1
    # Los binarios guardados deben seguir siendo ejecutables y seguros.
    validate_binary_path "${CACHED_PYBIN:-}" || return 1
    validate_binary_path "${CACHED_YTDLP_BIN:-}" || return 1
    command -v ffmpeg >/dev/null 2>&1 || return 1
    command -v ffprobe >/dev/null 2>&1 || return 1
    return 0
}

# Escribe la caché de dependencias tras una verificación exitosa.
# La escritura es atómica (archivo temporal + mv) para evitar lecturas a medias.
# @brief Guarda el estado verificado de las dependencias de forma atómica.
# @param $1 (no usado)
# @return 0 siempre (los fallos no son fatales).
# @side-effects Escribe y renombra $DEPS_CACHE_FILE en el directorio de caché.
write_deps_cache() {
    local tmp="" key="" value=""
    tmp="$(secure_mktemp "$OTTERRIP_CACHE_DIR" "deps_verified.env")" || return 0
    {
        printf 'CACHED_OTTERRIP_VERSION=%s\n' "$OTTERRIP_VERSION"
        printf 'CACHED_LIB_DIR=%s\n' "$LIB_DIR"
        printf 'CACHED_REQUIREMENTS_FILE=%s\n' "$REQUIREMENTS_FILE"
        printf 'CACHED_PYBIN=%s\n' "$PYBIN"
        printf 'CACHED_YTDLP_BIN=%s\n' "$YTDLP_BIN"
        printf 'CACHED_SYNCEDLYRICS_AVAILABLE=%s\n' "${SYNCEDLYRICS_AVAILABLE:-false}"
        local _js=""
        if ((${#JS_RUNTIME_ARGS[@]})); then
            _js="$(printf ' %s' "${JS_RUNTIME_ARGS[@]}")"
            _js="${_js:1}"
        fi
        printf 'CACHED_JS_ARGS=%s\n' "$_js"
        printf 'CACHED_TIMESTAMP=%s\n' "$(date +%s)"
    } > "$tmp" 2>/dev/null || { rm -f -- "$tmp"; return 0; }
    # Ningún valor con saltos de línea o caracteres de control.
    for key in "${DEPS_CACHE_KEYS[@]}"; do
        value="${!key:-}"
        if [[ "$value" =~ [[:cntrl:]] ]]; then
            rm -f -- "$tmp"
            return 0
        fi
    done
    chmod 600 -- "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$DEPS_CACHE_FILE" 2>/dev/null || rm -f -- "$tmp"
    return 0
}

install_deps() {
    local force_full=false
    [[ "${1-}" == "--check-deps" ]] && force_full=true

    if [[ "$force_full" == "false" ]] && deps_cache_valid; then
        # Restaurar todo el estado de ejecución desde la caché, igual que en el flujo completo.
        set_backend_dir "$(dirname -- "$CACHED_LIB_DIR")" || return 1
        PYBIN="$CACHED_PYBIN"
        export YTDLP_BIN="$CACHED_YTDLP_BIN"
        SYNCEDLYRICS_AVAILABLE="${CACHED_SYNCEDLYRICS_AVAILABLE:-false}"
        JS_RUNTIME_ARGS=()
        if [[ -n "${CACHED_JS_ARGS:-}" ]]; then
            read -r -a JS_RUNTIME_ARGS <<< "${CACHED_JS_ARGS}"
        fi
        msg_ok "Dependencias verificadas previamente (caché < 24 h)."
        return 0
    fi

    echo -e "\n${BOLD}${CYAN}══ Verificando dependencias ══${NC}\n"

    if ! command -v python3 >/dev/null 2>&1; then
        if confirm "python3 no está instalado. ¿Instalarlo ahora?"; then
            # shellcheck disable=SC2046
            pkg_install $(pkg_names_for "$(detect_pkg_manager 2>/dev/null || true)" python3) || true
        fi
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "  ${RED}✘${NC} python3 es obligatorio. Instálalo con: $(install_hint_for python3)"
        return 1
    fi
    if ! python_version_ok; then
        echo "  ${RED}✘${NC} Se requiere Python 3.9 o superior (detectado $(python3 --version 2>&1))."
        return 1
    fi
    echo "  ${GREEN}✔${NC} Python $(python3 --version 2>&1 | awk '{print $2}')"

    ensure_backend || return 1

    if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
        echo "  ${YELLOW}⚠${NC} ffmpeg/ffprobe no encontrados."
        if confirm "¿Instalar ffmpeg ahora (requiere permisos de administrador)?"; then
            pkg_install ffmpeg || pkg_install ffmpeg-free || true
        fi
    fi
    if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
        echo "  ${RED}✘${NC} ffmpeg y ffprobe son obligatorios."
        echo "    Instala con: $(install_hint_for ffmpeg)"
        echo "    Fedora sin RPM Fusion: sudo dnf install ffmpeg-free"
        return 1
    fi
    echo "  ${GREEN}✔${NC} ffmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"

    ensure_python_env || return 1
    if ! py_core --help >/dev/null 2>&1; then
        echo "  ${RED}✘${NC} El backend Python instalado no pasó la prueba de inicio."
        return 1
    fi
    echo "  ${GREEN}✔${NC} Dependencias Python listas ($([[ "$PYBIN" == "$VENV_DIR/bin/python" ]] && echo 'venv aislado' || echo 'Python del sistema'))"
    ensure_ytdlp || return 1
    setup_js_runtime

    refresh_syncedlyrics_state
    if [[ "$SYNCEDLYRICS_AVAILABLE" == "true" ]]; then
        echo "  ${GREEN}✔${NC} syncedlyrics disponible (Multi-Web activo)"
    else
        echo "  ${RED}✘${NC} syncedlyrics no instalado; Multi-Web no está disponible"
    fi

    # Avisos (yt-dlp antiguo, etc.) detectados por el diagnóstico
    py_core doctor 2>/dev/null | awk '/^  !/ { p=1; print; next } /^      →/ { if (p) print; next } { p=0 }' || true

    # ── Herramientas recomendadas para desarrollo ─────────────────
    check_dev_tools

    echo -e "\n${GREEN}${BOLD}  Dependencias listas.${NC}\n"
    log_event INFO "dependencias OK (python=$PYBIN yt-dlp=$YTDLP_BIN)"
    write_deps_cache
    sleep 0.6
}

# Verifica las herramientas de desarrollo recomendadas y ofrece instalarlas.
# No bloquea la ejecución: solo muestra avisos informativos.
# @brief Verifica herramientas de desarrollo recomendadas (shellcheck, bats).
# @param $1 (no usado)
# @return 0 siempre (no bloqueante).
# @side-effects Muestra avisos y ofrece instalar herramientas faltantes.
check_dev_tools() {
    [[ -t 1 ]] || return 0
    local -a missing=()
    local -a found=()

    # ShellCheck: análisis estático de scripts Bash
    if command -v shellcheck >/dev/null 2>&1; then
        found+=("shellcheck")
    else
        missing+=("shellcheck")
    fi

    # Bats: framework de pruebas para scripts Bash
    if command -v bats >/dev/null 2>&1; then
        found+=("bats")
    else
        missing+=("bats")
    fi

    # Pytest: framework de pruebas para Python (complementa unittest)
    if "$PYBIN" -m pytest --version >/dev/null 2>&1; then
        found+=("pytest")
    else
        missing+=("pytest")
    fi

    ((${#missing[@]} == 0)) && return 0

    echo -e "\n${BOLD}${CYAN}══ Herramientas de desarrollo recomendadas ══${NC}\n"

    local tool="" hint=""
    for tool in "${missing[@]}"; do
        case "$tool" in
            shellcheck)
                echo "  ${YELLOW}⚠${NC} shellcheck — análisis estático de scripts Bash"
                echo "    Instala con: $(install_hint_for shellcheck)"
                echo "    O desde fuente: https://github.com/koalaman/shellcheck"
                ;;
            bats)
                echo "  ${YELLOW}⚠${NC} bats — framework de pruebas para scripts Bash"
                echo "    Instala con: $(install_hint_for bats)"
                echo "    O desde fuente: https://github.com/bats-core/bats-core"
                ;;
            pytest)
                echo "  ${YELLOW}⚠${NC} pytest — framework de pruebas para Python"
                echo "    Instala con: $PYBIN -m pip install --user pytest"
                ;;
        esac
    done

    if confirm "¿Instalar las herramientas de desarrollo faltantes ahora?"; then
        local tool=""
        for tool in "${missing[@]}"; do
            case "$tool" in
                pytest)
                    echo "  Instalando pytest..."
                    "$PYBIN" -m pip install --user pytest 2>/dev/null ||
                        echo "  ${RED}✘${NC} Falló al instalar pytest. Usa: $PYBIN -m pip install --user pytest"
                    ;;
                *)
                    echo "  Instalando $tool..."
                    pkg_install "$(pkg_names_for "$(detect_pkg_manager 2>/dev/null || true)" "$tool")" ||
                        echo "  ${RED}✘${NC} Falló al instalar $tool. Usa: $(install_hint_for "$tool")"
                    ;;
            esac
        done
    fi

    if ((${#found[@]})); then
        echo -e "\n  ${GREEN}✔${NC} Herramientas disponibles: $(IFS=', '; echo "${found[*]}")"
    fi

    echo ""
    return 0
}

# ═══════════════════════════════════════════════════════════════
#  DETECCIÓN DE URL (SIN EVAL)
# ═══════════════════════════════════════════════════════════════
DETECTED_TYPE="song"
DETECTED_TITLE=""
DETECTED_COUNT="1"
DETECTION_FALLBACK=false

# Módulo de detección en Python (tools/detect_url.py). Está fuera de lib/ a
# propósito: lib/ es el contenido del paquete del backend y su digest está
# fijado por versión. Si el módulo no existe, se usa el heredoc de respaldo
# que hay al final de detect_url_type().
DETECT_SCRIPT="$SCRIPT_DIR/tools/detect_url.py"

# Ejecuta un comando con límite de tiempo. Devuelve 124 si se agota.
# Usa `timeout` del sistema; si no existe, un vigilante en segundo plano.
run_with_timeout() {
    local secs="${1-}"
    shift || true
    (($#)) || return 0
    if command -v timeout >/dev/null 2>&1; then
        timeout "$secs" "$@"
        return $?
    fi
    local outfile="" pid="" waited=0 rc=0
    outfile="$(mktemp "${TMPDIR:-/tmp}/otterrip-timeout.$$.XXXXXX" 2>/dev/null || true)"
    if [[ -z "$outfile" ]]; then
        "$@"
        return $?
    fi
    "$@" >"$outfile" 2>/dev/null &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if (( waited >= secs )); then
            kill -s TERM -- "$pid" 2>/dev/null || true
            sleep 0.3
            kill -s KILL -- "$pid" 2>/dev/null || true
            rc=124
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
    wait "$pid" 2>/dev/null || rc=$?
    cat -- "$outfile" 2>/dev/null || true
    rm -f -- "$outfile" 2>/dev/null || true
    return "$rc"
}

# Detecta si la URL es canción, playlist o álbum.
# Rellena DETECTED_TYPE, DETECTED_COUNT, DETECTED_TITLE y DETECTION_FALLBACK.
# @brief Detecta si una URL es canción, playlist o álbum.
# @param $1 URL a analizar.
# @return 0 siempre; rellena DETECTED_TYPE, DETECTED_COUNT, DETECTED_TITLE y
#         DETECTION_FALLBACK.
# @side-effects Consulta yt-dlp (con límite de tiempo) y hace una petición HTTP.
detect_url_type() {
    local url="${1-}" lower="${1,,}" no_playlist=0
    local -a lines=()
    local raw="" rc=0
    DETECTED_TYPE="song"
    DETECTED_TITLE=""
    DETECTED_COUNT="1"
    DETECTION_FALLBACK=false

    # Una URL que empieza por "-" podría interpretarse como una opción de
    # yt-dlp: se rechaza antes de tocar la red.
    if ! is_safe_url "$url"; then
        DETECTION_FALLBACK=true
        return 0
    fi

    # Un enlace /watch?...&list=... se trata como canción (igual que v6.1).
    if [[ "$lower" =~ ^https?://(www\.)?(youtube\.com|youtu\.be)/watch\? ]] && [[ "$lower" == *"list="* ]]; then
        no_playlist=1
    fi

    # ── Módulo externo (preferido) ──────────────────────────────
    if [[ -f "$DETECT_SCRIPT" && -r "$DETECT_SCRIPT" ]]; then
        local -a det_cmd=("$DETECT_SCRIPT" --url "$url" --timeout "$DETECT_PY_TIMEOUT")
        (( no_playlist == 1 )) && det_cmd+=(--no-playlist)
        [[ -n "${YTDLP_BIN:-}" ]] && det_cmd+=(--ytdlp "$YTDLP_BIN")
        [[ -n "${JS_RUNTIME_ARGS[*]:-}" ]] && det_cmd+=(--js-args "${JS_RUNTIME_ARGS[*]}")
        [[ -n "${COOKIES_FILE:-}" ]] && det_cmd+=(--cookies-file "$COOKIES_FILE")
        [[ -n "${COOKIES_BROWSER:-}" ]] && det_cmd+=(--cookies-browser "$COOKIES_BROWSER")
        raw="$(run_with_timeout "$DETECT_TIMEOUT" "${PYBIN:-python3}" "${det_cmd[@]}" 2>/dev/null)" || rc=$?
    else
        # ── Respaldo: Python embebido ───────────────────────────
        local DETECT_URL="$url" DETECT_NO_PLAYLIST="$no_playlist"
        local DETECT_JS_ARGS="${JS_RUNTIME_ARGS[*]:-}"
        local DETECT_COOKIES_FILE="${COOKIES_FILE:-}" DETECT_COOKIES_BROWSER="${COOKIES_BROWSER:-}"
        export DETECT_URL DETECT_NO_PLAYLIST DETECT_JS_ARGS DETECT_COOKIES_FILE DETECT_COOKIES_BROWSER
        raw="$(run_with_timeout "$DETECT_TIMEOUT" "${PYBIN:-python3}" - <<'PYEOF' 2>/dev/null
import json, os, re, subprocess, sys

url = os.environ.get("DETECT_URL", "")
no_playlist = os.environ.get("DETECT_NO_PLAYLIST", "0") == "1"
ytdlp_bin = os.environ.get("YTDLP_BIN", "").strip()
js_args = [a for a in os.environ.get("DETECT_JS_ARGS", "").split() if a]
cookies_file = os.environ.get("DETECT_COOKIES_FILE", "").strip()
cookies_browser = os.environ.get("DETECT_COOKIES_BROWSER", "").strip()

def url_hint(value: str) -> str:
    u = value.lower()
    album = "/album/" in u or "/albums/" in u or "olak5uy_" in u
    collection = album or "/playlist" in u or "/sets/" in u or "list=" in u
    if no_playlist and "/watch" in u:
        collection = False
    return "album" if album else ("playlist" if collection else "song")

def clean(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()

def decode_field(value: str) -> str:
    value = (value or "").strip()
    if not value or value == "NA":
        return ""
    try:
        obj = json.loads(value)
    except Exception:
        return value
    if obj is None:
        return ""
    return str(obj)

def print_fallback() -> None:
    kind = url_hint(url)
    print(kind)
    print("1" if kind == "song" else "")
    print("")
    print("fallback")

fields = [
    "playlist_count", "playlist_title", "album", "title",
    "webpage_url_domain", "playlist_id", "webpage_url", "_type",
]
template = "\x1f".join(f"%({name})j" for name in fields)
base = [ytdlp_bin] if ytdlp_bin else [sys.executable, "-m", "yt_dlp"]

def try_run(flat: bool):
    args = base + [
        "--playlist-items", "1",
        "--skip-download", "--no-warnings", "--ignore-errors",
        "--socket-timeout", "20",
        "--print", template,
    ]
    args.extend(js_args)
    if cookies_file and os.path.isfile(cookies_file) and os.access(cookies_file, os.R_OK):
        args.extend(["--cookies", cookies_file])
    elif cookies_browser:
        args.extend(["--cookies-from-browser", cookies_browser])
    if flat:
        args.insert(len(base), "--flat-playlist")
    if no_playlist:
        args.append("--no-playlist")
    # "--" evita que la URL se interprete como una opción de yt-dlp.
    args.append("--")
    args.append(url)
    try:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=50,
        )
    except Exception:
        return ""
    for line in (proc.stdout or "").splitlines():
        if line.count("\x1f") >= len(fields) - 1:
            return line
    return ""

line = try_run(True) or try_run(False)
if not line:
    print_fallback()
    raise SystemExit(0)

parts = line.split("\x1f")
while len(parts) < len(fields):
    parts.append("")
vals = [decode_field(p) for p in parts[:len(fields)]]

raw_count = vals[0]
count = int(raw_count) if raw_count.isdigit() else 0
ptitle, album, title, domain, pid, page, typ = vals[1], vals[2], vals[3], vals[4], vals[5], vals[6], vals[7]
url_l = url.lower()
page_l = page.lower()

pure_collection = any(x in url_l for x in ("/playlist", "/sets/", "/album/", "/albums/"))
collection = bool(ptitle or pid) or count > 1 or pure_collection
if no_playlist and not pure_collection:
    collection = False

album_flag = (
    "/album/" in page_l or "/albums/" in page_l
    or "/album/" in url_l or "/albums/" in url_l
    or ("music.youtube.com" in domain.lower() and pid.startswith("OLAK5uy_"))
    or (album and ptitle and album.casefold().strip() == ptitle.casefold().strip())
)

kind = "album" if (collection and album_flag) else ("playlist" if collection else "song")
out_title = (ptitle or album or title) if collection else title
out_count = (count if count > 0 else "") if collection else 1
print(kind)
print(out_count)
print(clean(out_title))
print("ok")
PYEOF
        )" || rc=$?
    fi

    # Tiempo agotado (124) o fallo: se recurre a la detección por URL.
    if (( rc != 0 )) || [[ -z "$raw" ]]; then
        DETECTION_FALLBACK=true
    else
        if [[ -n "$raw" ]]; then
            mapfile -t lines <<< "$raw"
        fi
    fi

    if ((${#lines[@]} >= 3)); then
        case "${lines[0],,}" in
            song|playlist|album) DETECTED_TYPE="${lines[0],,}" ;;
            *) DETECTED_TYPE="song" ;;
        esac
        DETECTED_COUNT="$(trim_input "${lines[1]}")"
        DETECTED_TITLE="$(trim_input "${lines[2]}")"
        if [[ "${lines[3]:-ok}" == "fallback" ]]; then
            DETECTION_FALLBACK=true
        fi
    else
        DETECTION_FALLBACK=true
        if [[ "$lower" == *"/album/"* || "$lower" == *"/albums/"* ]]; then
            DETECTED_TYPE="album"
        elif [[ "$lower" == *"/playlist"* || "$lower" == *"/sets/"* ]]; then
            DETECTED_TYPE="playlist"
        else
            DETECTED_TYPE="song"
        fi
    fi

    if [[ "$DETECTED_TYPE" == "song" ]]; then
        DETECTED_COUNT="1"
    elif ! [[ "$DETECTED_COUNT" =~ ^[0-9]+$ ]]; then
        DETECTED_COUNT=""
    fi
    return 0
}

detected_type_label() {
    case "${1-}" in
        song) printf '%s' "Canción individual" ;;
        playlist) printf '%s' "Playlist" ;;
        album) printf '%s' "Álbum" ;;
        *) printf '%s' "Desconocido" ;;
    esac
}

# Caché de conteo de playlists: URL (hash SHA-256) → número de elementos.
declare -gA URL_COUNT_CACHE=()
declare -gA URL_COUNT_TIME=()

# Clave de caché para una URL (hash SHA-256; si no hay sha256sum, cksum).
url_cache_key() {
    local url="${1-}" key=""
    key="$(printf '%s' "$url" | sha256sum 2>/dev/null | awk '{print $1}')"
    if [[ -z "$key" ]]; then
        key="$(printf '%s' "$url" | shasum -a 256 2>/dev/null | awk '{print $1}')"
    fi
    if [[ -z "$key" ]]; then
        key="$(printf '%s' "$url" | cksum 2>/dev/null | awk '{print $1}')"
    fi
    [[ -n "$key" ]] || key="$url"
    printf '%s' "$key"
}

# @brief Cuenta los elementos de una colección (con caché de sesión).
# @param $1 URL de la colección.
# @return 0 e imprime el número de elementos (0 si no se pudo determinar).
# @side-effects Consulta yt-dlp y guarda el resultado en la caché en memoria.
count_playlist_entries() {
    local url="${1-}" n=0
    [[ -n "$url" ]] || { printf '0'; return; }
    # Una URL que empieza por "-" podría ser interpretada como una opción.
    is_safe_url "$url" || { printf '0'; return; }

    # Caché de sesión: evita repetir el conteo para la misma URL.
    local key="" now="" ts=0
    key="$(url_cache_key "$url")"
    now="$(date +%s)"
    if [[ -n "${URL_COUNT_CACHE[$key]:-}" ]]; then
        ts="${URL_COUNT_TIME[$key]:-0}"
        if [[ "$ts" =~ ^[0-9]+$ ]] && (( now - ts < URL_COUNT_TTL )); then
            printf '%s' "${URL_COUNT_CACHE[$key]}"
            return 0
        fi
    fi

    local -a yt_args=(--flat-playlist --skip-download --no-warnings --ignore-errors --socket-timeout 20)
    if ((${#JS_RUNTIME_ARGS[@]})); then
        yt_args+=( "${JS_RUNTIME_ARGS[@]}" )
    fi
    # Cookies para evitar bloqueos de YouTube durante el conteo.
    if [[ -n "$COOKIES_FILE" && -r "$COOKIES_FILE" ]]; then
        yt_args+=( --cookies "$COOKIES_FILE" )
    elif [[ -n "$COOKIES_BROWSER" ]]; then
        yt_args+=( --cookies-from-browser "$COOKIES_BROWSER" )
    fi
    n="$( { ytdlp "${yt_args[@]}" \
            --print "%(id)s" -- "$url" 2>/dev/null || true; } \
          | awk 'NF { c++ } END { print c+0 }')"
    n="${n//[[:space:]]/}"
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    URL_COUNT_CACHE["$key"]="$n"
    URL_COUNT_TIME["$key"]="$now"
    printf '%s' "$n"
}

# ═══════════════════════════════════════════════════════════════
#  SESIONES, LOGS Y BLOQUEOS
# ═══════════════════════════════════════════════════════════════
count_log_pattern() {
    local pattern="${1-}" n=0
    shift || true
    (($#)) || { printf '0'; return; }
    n="$( { grep -hE "$pattern" "$@" 2>/dev/null || true; } \
          | LC_ALL=C sort -u | wc -l )"
    n="${n//[[:space:]]/}"
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    printf '%s' "$n"
}

# Tamaño total (en bytes) de varios archivos con UNA sola llamada a wc.
logs_total_size() {
    (($#)) || { printf '0'; return; }
    local out=""
    out="$(wc -c -- "$@" 2>/dev/null | tail -n1 | tr -cd '0-9')"
    [[ "$out" =~ ^[0-9]+$ ]] || out=0
    printf '%s' "$out"
}

SKIP_PATTERN='has already been recorded in the archive|has already been downloaded|does not pass filter|doesn.t pass filter'

show_last_errors() {
    local max="${1:-5}"
    shift || true
    (($#)) || return 0
    local -a lines=()
    mapfile -t lines < <( { grep -h '^ERROR:' "$@" 2>/dev/null || true; } \
        | LC_ALL=C sort -u | tail -n "$max" )
    ((${#lines[@]})) || return 0
    echo -e "  ${YELLOW}Errores detectados (resumen):${NC}"
    local line
    for line in "${lines[@]}"; do
        printf '    %s\n' "${line:0:220}"
    done
}

# Valor de `clave=valor` en la salida de classify-log.
class_value() {
    local key="${1-}"
    shift || true
    py_core classify-log "$@" 2>/dev/null | awk -F= -v k="$key" '$1==k { print $2; exit }'
}

session_dir_has_entries() {
    local d="${1-}" f
    for f in "$d"/session.*; do
        [[ -f "$f" ]] || continue
        [[ "$(count_lines "$f")" -gt 0 ]] && return 0
    done
    return 1
}

# Normaliza un archivo de sesión a otro con un identificador de vídeo por
# línea. El backend (verify-session) busca el ID DENTRO del nombre del archivo,
# por lo que necesita la lista plana; la plantilla de sesión genera un JSON
# rico que hay que reducir. Acepta ambos formatos (JSON o ID suelto) para
# poder reutilizar sesiones de versiones anteriores.
session_to_ids() {
    local src="${1-}" dst="${2-}"
    [[ -n "$src" && -n "$dst" && -f "$src" ]] || return 1
    awk '
        {
            line = $0
            sub(/\r$/, "", line)
            if (line ~ /^\{/) {
                if (match(line, /"id"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
                    id = substr(line, RSTART, RLENGTH)
                    sub(/^.*"id"[[:space:]]*:[[:space:]]*"/, "", id)
                    sub(/"$/, "", id)
                    if (id != "") { print id; next }
                }
            }
            if (line != "") print line
        }
    ' "$src" 2>/dev/null | LC_ALL=C sort -u > "$dst" 2>/dev/null || return 1
    return 0
}

# Bloqueo por carpeta de destino: evita dos instancias escribiendo el mismo
# archivo de historial. Si flock no existe se usa un bloqueo atómico por
# directorio (mkdir), nunca se deja el destino desprotegido.
DEST_LOCK_DIR=""

# Devuelve "<dispositivo> <inode>" del descriptor indicado (o de una ruta).
fd_identity() {
    local fd="${1-}" fallback="${2-}" out=""
    if [[ -n "$fd" ]]; then
        out="$(stat -L -c '%d %i' -- "/proc/self/fd/$fd" 2>/dev/null || true)"
    fi
    if [[ -z "$out" && -n "$fallback" ]]; then
        out="$(stat -L -c '%d %i' -- "$fallback" 2>/dev/null || true)"
    fi
    printf '%s' "$out"
}

# @brief Toma un bloqueo exclusivo sobre la carpeta de destino.
# @param $1 Carpeta de destino que se quiere proteger.
# @return 0 si se obtuvo el bloqueo; 1 si se agotó el tiempo o falló.
# @side-effects Crea $dest/.otterrip.lock (flock) o $dest/.otterrip.lock.d (mkdir).
acquire_dest_lock() {
    local dest="${1-}"
    local lockfile="$dest/.otterrip.lock"
    if [[ ! -d "$dest" ]]; then
        log_event WARN "destino inexistente al bloquear: $dest"
        return 0
    fi
    if command -v flock >/dev/null 2>&1; then
        { exec {DEST_LOCK_FD}>"$lockfile"; } 2>/dev/null || { DEST_LOCK_FD=""; return 0; }
        # Identidad del inode ANTES de bloquear: se compara después para
        # detectar que el archivo fue sustituido (TOCTOU).
        local id_before="" id_after=""
        id_before="$(fd_identity "$DEST_LOCK_FD" "$lockfile")"
        if ! flock -n "$DEST_LOCK_FD"; then
            echo -e "  ${YELLOW}⏳${NC} Otra instancia de OtterRip usa este destino; esperando (máx. ${LOCK_TIMEOUT} s)..."
            if ! flock -w "$LOCK_TIMEOUT" "$DEST_LOCK_FD"; then
                echo -e "  ${RED}✘${NC} No se pudo obtener el bloqueo del destino."
                { exec {DEST_LOCK_FD}>&-; } 2>/dev/null || true
                DEST_LOCK_FD=""
                return 1
            fi
        fi
        # Verificación TOCTOU: el descriptor debe seguir apuntando al mismo inode.
        id_after="$(fd_identity "$DEST_LOCK_FD" "$lockfile")"
        if [[ -n "$id_before" && -n "$id_after" && "$id_before" != "$id_after" ]]; then
            echo -e "  ${RED}✘${NC} El archivo de bloqueo fue sustituido; se aborta por seguridad."
            { exec {DEST_LOCK_FD}>&-; } 2>/dev/null || true
            DEST_LOCK_FD=""
            return 1
        fi
        return 0
    fi

    # Sin flock: bloqueo atómico por directorio (mkdir es atómico en POSIX).
    local lockdir="$dest/.otterrip.lock.d" owner="" waited=0
    while ! mkdir -- "$lockdir" 2>/dev/null; do
        owner="$(cat -- "$lockdir/pid" 2>/dev/null || true)"
        if [[ "$owner" =~ ^[0-9]+$ ]] && ! kill -0 "$owner" 2>/dev/null; then
            rm -f -- "$lockdir/pid" 2>/dev/null || true
            rmdir -- "$lockdir" 2>/dev/null || true
            continue
        fi
        if (( waited >= LOCK_TIMEOUT )); then
            echo -e "  ${RED}✘${NC} No se pudo obtener el bloqueo del destino (${LOCK_TIMEOUT} s)."
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    if ! printf '%s\n' "$$" > "$lockdir/pid" 2>/dev/null; then
        rmdir -- "$lockdir" 2>/dev/null || true
        echo -e "  ${RED}✘${NC} No se pudo registrar el bloqueo del destino."
        return 1
    fi
    DEST_LOCK_DIR="$lockdir"
    return 0
}

# @brief Libera el bloqueo del destino tomado por acquire_dest_lock.
# @param $1 (no usado)
# @return 0 siempre.
# @side-effects Cierra el descriptor y/o elimina el directorio de bloqueo.
release_dest_lock() {
    if [[ -n "${DEST_LOCK_FD:-}" ]]; then
        { exec {DEST_LOCK_FD}>&-; } 2>/dev/null || true
        DEST_LOCK_FD=""
    fi
    if [[ -n "${DEST_LOCK_DIR:-}" ]]; then
        rm -f -- "$DEST_LOCK_DIR/pid" 2>/dev/null || true
        rmdir -- "$DEST_LOCK_DIR" 2>/dev/null || true
        DEST_LOCK_DIR=""
    fi
    return 0
}

# Elimina SOLO temporales propios (.<nombre>.otterrip-<pid>.tmp.*) de hace >1 h.
clean_stale_temp() {
    local dest="${1-}"
    [[ -d "$dest" ]] || return 0
    find "$dest" -maxdepth 4 -type f -name '.*.otterrip-[0-9]*.tmp*' -mmin +60 -delete 2>/dev/null || true
    return 0
}

queue_cancelled() {
    [[ -n "$QUEUE_TASK_ID" && -e "$OTTERRIP_STATE_DIR/cancel/$QUEUE_TASK_ID" ]]
}

# ═══════════════════════════════════════════════════════════════
#  METADATOS, LETRAS Y VERIFICACIÓN (núcleo Python)
# ═══════════════════════════════════════════════════════════════
fix_metadata() {
    local target="${1-}" fmt="${2-}" session_file="${3-}" cover_path="${4-}"
    local -a args=(fix-metadata "$target" "$fmt")
    [[ -n "$session_file" ]] && args+=(--session "$session_file")
    [[ -n "$cover_path" ]] && args+=(--cover "$cover_path")
    py_core "${args[@]}"
}

# Opciones de letras comunes (descarga y gestor avanzado).
lyrics_common_args() {
    LYRICS_ARGS=(--host "$LRCLIB_HOST" --mode "$LYRICS_MODE" --providers "$LYRICS_PROVIDERS" --synced-dir "$SYNCED_DIR")
    [[ -n "$LYRICS_LANG" ]] && LYRICS_ARGS+=(--lang "$LYRICS_LANG")
    [[ "$LYRICS_PREFER_SYNCED" == "false" ]] && LYRICS_ARGS+=(--plain-ok)
    [[ "$LYRICS_UPGRADE_PLAIN" == "true" ]] && LYRICS_ARGS+=(--upgrade-plain)
    [[ "$LYRICS_SYNC_LEVEL" != "line" ]] && LYRICS_ARGS+=(--sync-level "$LYRICS_SYNC_LEVEL")
    [[ "$LYRICS_SIDECAR" == "true" ]] && LYRICS_ARGS+=(--sidecar)
    [[ -n "$LYRICS_EMBED_STYLE" ]] && LYRICS_ARGS+=(--embed-style "$LYRICS_EMBED_STYLE")
    if (( LYRICS_MIN_CONFIDENCE > 0 )); then
        # LYRICS_MIN_CONFIDENCE stores tenths-of-percent (600 = 60% = 0.6)
        # Convert to float: 600 -> "0.6"
        local conf_int=$(( LYRICS_MIN_CONFIDENCE / 10 ))
        local conf_dec=$(( LYRICS_MIN_CONFIDENCE % 10 ))
        LYRICS_ARGS+=(--min-confidence "${conf_int}.${conf_dec}")
    fi
    if (( LYRICS_OFFSET_MS != 0 )); then
        LYRICS_ARGS+=(--offset-ms "$LYRICS_OFFSET_MS")
    fi
    return 0
}

# Combina dos resúmenes de letras (found/notfound/already) en el principal.
# El segundo resumen corresponde al reintento en modo plano: las pistas que ya
# tenían letra pasan a "already" y las nuevas se suman a "found".
merge_lyrics_summary() {
    local main="${1-}" extra="${2-}"
    [[ -n "$main" && -f "$main" ]] || return 0
    [[ -n "$extra" && -f "$extra" ]] || return 0
    local f1 a1 f2 n2 a2
    f1="$(summary_value "$main" found 0)"
    a1="$(summary_value "$main" already 0)"
    f2="$(summary_value "$extra" found 0)"
    n2="$(summary_value "$extra" notfound 0)"
    a2="$(summary_value "$extra" already 0)"
    printf 'found=%s\nnotfound=%s\nalready=%s\n' \
        "$(( f1 + f2 ))" "$n2" "$(( a1 + a2 ))" > "$main" 2>/dev/null || true
    return 0
}

process_lyrics() {
    local dir="${1-}" fmt="${2-}" session_file="${3-}" summary_file="${4-}"
    local rc=0
    if [[ "$FETCH_LYRICS" != "true" ]]; then
        if [[ -n "$summary_file" ]]; then
            printf 'found=0\nnotfound=0\nalready=0\n' > "$summary_file" 2>/dev/null || true
        fi
        return 0
    fi
    lyrics_common_args
    local -a args=(lyrics "$dir" "$fmt" "${LYRICS_ARGS[@]}" --jobs "$PARALLEL_JOBS")
    [[ -n "$session_file" ]] && args+=(--session "$session_file")
    [[ -n "$summary_file" ]] && args+=(--summary "$summary_file")
    py_core "${args[@]}" || rc=$?

    # Segundo intento: si quedan pistas sin letra se reintenta aceptando
    # también letras planas. Por defecto solo se aceptan letras sincronizadas
    # (con marcas de tiempo), así que una canción que solo tenga letra plana
    # se descartaría en el primer intento aunque exista en alguna fuente.
    if [[ -n "$summary_file" && "$LYRICS_PREFER_SYNCED" == "true" ]]; then
        local pending=0
        pending="$(summary_value "$summary_file" notfound 0)"
        if [[ "$pending" =~ ^[0-9]+$ ]] && (( pending > 0 )); then
            # Sin --upgrade-plain: el reintento no debe reemplazar letras ya
            # incrustadas (las que ya tienen letra se saltan como "already").
            local -a plain_args=()
            local i=0
            for ((i = 0; i < ${#LYRICS_ARGS[@]}; i++)); do
                [[ "${LYRICS_ARGS[$i]}" == "--upgrade-plain" ]] && continue
                plain_args+=("${LYRICS_ARGS[$i]}")
            done
            local retry_summary="${summary_file}.plain"
            local -a retry=(lyrics "$dir" "$fmt" "${plain_args[@]}" --jobs "$PARALLEL_JOBS" --plain-ok)
            [[ -n "$session_file" ]] && retry+=(--session "$session_file")
            retry+=(--summary "$retry_summary")
            echo -e "  ${DIM}· Segundo intento (letra plana) en ${pending} pista(s) sin letras...${NC}"
            py_core "${retry[@]}" >/dev/null 2>&1 || true
            merge_lyrics_summary "$summary_file" "$retry_summary"
            rm -f -- "$retry_summary" 2>/dev/null || true
        fi
    fi

    case "$rc" in
        0) return 0 ;;
        14) echo "  ${YELLOW}⚠${NC} El servicio de letras no estaba disponible (se puede reintentar más tarde)." ;;
        *) echo "  ${YELLOW}⚠${NC} El procesamiento de letras terminó con errores (código $rc)." ;;
    esac
    return 1
}

# ═══════════════════════════════════════════════════════════════
#  MENÚS DE CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════
album_layout_label() {
    if [[ "$ALBUM_LAYOUT" == "single" ]]; then
        printf '%s' "Todos los temas en una sola carpeta"
    else
        printf '%s' "Una carpeta por álbum"
    fi
}

menu() {
    while true; do
        clear_screen
        local lyrics_state mode_label dest provider
        if [[ "$FETCH_LYRICS" == "true" ]]; then
            lyrics_state="${GREEN}✔ ON${NC}"
        else
            lyrics_state="${RED}✘ OFF${NC}"
        fi
        if [[ "$LYRICS_MODE" == "embed" ]]; then
            mode_label="embed (tags)"
        else
            mode_label="file (.lrc/.txt)"
        fi
        provider="$(provider_label)"
        dest="$(destination_display)"

        echo -e "${BOLD}${CYAN}"
        echo "  ╔══════════════════════════════════════════════════╗"
        echo "  ║                 🦦 OTTER RIP v${OTTERRIP_VERSION}              ║"
        echo "  ╚══════════════════════════════════════════════════╝${NC}"
        printf '  %sPerfil:%s      %s\n' "$BOLD" "$NC" "$PROFILE"
        printf '  %sDuración:%s    >%ss y ≤ %s\n' "$BOLD" "$NC" "$DUR_MIN" "$(format_seconds "$DUR_MAX")"
        printf '  %sWorkers:%s     %s (descargas y letras)\n' "$BOLD" "$NC" "$PARALLEL_JOBS"
        printf '  %sLetras:%s      %b (%s)\n' "$BOLD" "$NC" "$lyrics_state" "$mode_label"
        printf '  %sProveedor:%s   %s\n' "$BOLD" "$NC" "$provider"
        printf '  %sServidor:%s    %s\n' "$BOLD" "$NC" "$LRCLIB_HOST"
        printf '  %sÁlbum:%s       %s\n' "$BOLD" "$NC" "$(album_layout_label)"
        if [[ -n "$COVER_LOCAL" ]]; then
            printf '  %sPortada:%s     %s\n' "$BOLD" "$NC" "$COVER_LOCAL"
        else
            printf '  %sPortada:%s     AUTO (thumbnail)\n' "$BOLD" "$NC"
        fi
        if [[ -n "$COOKIES_FILE" ]]; then
            printf '  %sCookies:%s     Archivo (%s)\n' "$BOLD" "$NC" "$COOKIES_FILE"
        elif [[ -n "$COOKIES_BROWSER" ]]; then
            printf '  %sCookies:%s     Navegador (%s)\n' "$BOLD" "$NC" "$COOKIES_BROWSER"
        fi
        printf '  %sDestino:%s     %s\n' "$BOLD" "$NC" "$dest"
        if [[ -n "$OTTERRIP_PROXY" ]]; then
            printf '  %sProxy:%s      %s\n' "$BOLD" "$NC" "$OTTERRIP_PROXY"
        fi
        echo ""
        echo -e "  ${CYAN} 1)${NC} Descargar audio"
        echo -e "  ${CYAN} 2)${NC} Configurar calidad"
        echo -e "  ${CYAN} 3)${NC} Configurar límites de duración"
        echo -e "  ${CYAN} 4)${NC} Configurar descargas paralelas (workers)"
        echo -e "  ${CYAN} 5)${NC} Configurar carpeta de álbum"
        echo -e "  ${CYAN} 6)${NC} Configurar portada manual"
        echo -e "  ${CYAN} 7)${NC} Configurar LETRAS (LRCLIB & Multi-Web)"
        echo -e "  ${CYAN} 8)${NC} Guía de calidad"
        echo -e "  ${CYAN} 9)${NC} Abrir carpeta downloads"
        echo -e "  ${CYAN}10)${NC} Cola de descargas"
        echo -e "  ${CYAN}11)${NC} Biblioteca musical (informe, duplicados)"
        echo -e "  ${CYAN}12)${NC} Reparador de metadatos"
        echo -e "  ${CYAN}13)${NC} Gestor avanzado de letras"
        echo -e "  ${CYAN}14)${NC} Conversor de audio (archivos locales)"
        echo -e "  ${CYAN}15)${NC} Diagnóstico y mantenimiento"
        echo -e "  ${CYAN}16)${NC} Configurar cookies (YouTube / anti-bot)"
        echo -e "  ${CYAN}17)${NC} Ver historial de descargas"
        echo -e "  ${CYAN}18)${NC} Configurar proxy de red"
        echo -e "  ${CYAN} 0)${NC} Salir"
        echo ""
        read -rp "  Opción: " op || exit 0
        case "$op" in
            1) menu_download ;;
            2) menu_quality ;;
            3) menu_duration ;;
            4) menu_workers ;;
            5) menu_album_layout ;;
            6) menu_cover ;;
            7) menu_lyrics ;;
            8) menu_guide ;;
            9)
                xdg-open "$DOWNLOADS" >/dev/null 2>&1 \
                    || thunar "$DOWNLOADS" >/dev/null 2>&1 \
                    || printf '  %s\n' "$DOWNLOADS"
                sleep 1
                ;;
            10) menu_queue ;;
            11) menu_library ;;
            12) menu_repair ;;
            13) menu_lyrics_manager ;;
            14) menu_convert ;;
            15) menu_diagnostics ;;
            16) menu_cookies ;;
            17) show_history 50; pause_enter ;;
            18) menu_proxy ;;
            0) save_config; echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
        save_config
    done
}

# Configuración del proxy de red usado por yt-dlp.
menu_proxy() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Proxy de red ══${NC}\n"
    echo -e "  Proxy usado por yt-dlp para todas las peticiones."
    echo -e "  Formatos: http:// · https:// · socks4:// · socks5://\n"
    printf '  %sActual:%s %s\n\n' "$BOLD" "$NC" "${OTTERRIP_PROXY:-ninguno}"
    local raw="" value=""
    read -rp "  URL del proxy (Enter=desactivar): " raw || return 0
    raw="$(trim_input "$(strip_wrapping_quotes "$raw")")"
    if [[ -z "$raw" ]]; then
        OTTERRIP_PROXY=""
        msg_ok "Proxy desactivado."
        sleep 0.8
        return 0
    fi
    if value="$(normalize_proxy "$raw")"; then
        OTTERRIP_PROXY="$value"
        msg_ok "Proxy: $OTTERRIP_PROXY"
    else
        msg_err "Proxy no válido. Debe ser http://, https://, socks4:// o socks5://"
    fi
    sleep 1
}

menu_quality() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Calidad de audio ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} MP3  320 kbps"
    echo -e "  ${CYAN}2)${NC} M4A  AAC 256 kbps"
    echo -e "  ${CYAN}3)${NC} M4A  AAC 320 kbps"
    echo -e "  ${CYAN}4)${NC} FLAC 16-bit / 44.1 kHz"
    echo -e "  ${CYAN}5)${NC} FLAC 24-bit / 96 kHz"
    echo -e "  ${CYAN}0)${NC} Volver"
    echo ""
    read -rp "  Opción: " q || return 0
    case "$q" in
        1|2|3|4|5) apply_quality_choice "$q" ;;
        0|"") return 0 ;;
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1; return 0 ;;
    esac
    echo -e "  ${GREEN}✔ $PROFILE${NC}"
    sleep 0.8
}

menu_duration() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Límites de duración ══${NC}\n"
    echo -e "  Solo se descargan audios que cumplan:"
    echo -e "  ${GREEN}duración > mínima${NC}  y  ${GREEN}duración ≤ máxima${NC}"
    echo ""
    printf '  Actual → mín: %s%ss%s   máx: %s%s (%ss)%s\n\n' \
        "$BOLD" "$DUR_MIN" "$NC" "$BOLD" "$(format_seconds "$DUR_MAX")" "$DUR_MAX" "$NC"
    local dmin="" dmax=""
    read -rp "  Duración mínima en segundos (Enter=$DUR_MIN): " dmin || return 0
    read -rp "  Duración máxima en segundos (Enter=$DUR_MAX): " dmax || return 0
    [[ -z "$dmin" ]] && dmin="$DUR_MIN"
    [[ -z "$dmax" ]] && dmax="$DUR_MAX"
    if ! is_positive_int "$dmin" || ! is_positive_int "$dmax"; then
        echo -e "  ${RED}✘ Deben ser enteros positivos.${NC}"; sleep 1.5; return 0
    fi
    dmin=$((10#$dmin)); dmax=$((10#$dmax))
    if (( dmin >= dmax )); then
        echo -e "  ${RED}✘ La duración mínima debe ser menor que la máxima.${NC}"; sleep 1.5; return 0
    fi
    DUR_MIN="$dmin"; DUR_MAX="$dmax"
    echo -e "\n  ${GREEN}✔ Rango: >${DUR_MIN}s y ≤ ${DUR_MAX}s${NC}"
    sleep 0.8
}

menu_workers() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Descargas paralelas ══${NC}\n"
    echo -e "  Workers simultáneos para playlists, álbumes y letras por pista."
    echo -e "  ${DIM}1 = secuencial · 2-32 = descarga paralela real${NC}"
    echo -e "  ${DIM}Más workers aumentan el uso de red/CPU y las solicitudes a los servicios.${NC}"
    echo ""
    printf '  Actual: %s%s worker(s)%s\n\n' "$BOLD" "$PARALLEL_JOBS" "$NC"
    local w="" cpus=1
    read -rp "  Workers [1-32] (Enter=$PARALLEL_JOBS): " w || return 0
    [[ -z "$w" ]] && return 0
    if ! is_positive_int "$w"; then
        echo -e "  ${RED}✘ Valor inválido. Debe estar entre 1 y 32.${NC}"; sleep 1.5; return 0
    fi
    while [[ ${#w} -gt 1 && "${w:0:1}" == "0" ]]; do
        w="${w:1}"
    done
    if [[ ${#w} -gt 2 ]] || (( 10#$w > MAX_WORKERS )); then
        echo -e "  ${RED}✘ Valor inválido. Debe estar entre 1 y 32.${NC}"; sleep 1.5; return 0
    fi
    PARALLEL_JOBS=$((10#$w))
    cpus="$(nproc 2>/dev/null || printf '1')"
    [[ "$cpus" =~ ^[0-9]+$ ]] || cpus=1
    if (( PARALLEL_JOBS == 1 )); then
        echo -e "  ${GREEN}✔ Modo secuencial.${NC}"
    else
        echo -e "  ${GREEN}✔ Paralelo con $PARALLEL_JOBS workers.${NC}"
        if (( PARALLEL_JOBS > cpus )); then
            echo -e "  ${YELLOW}⚠ Tu equipo tiene $cpus CPU(s): más workers que CPUs puede ralentizar la conversión.${NC}"
            sleep 1.5
        fi
    fi
    sleep 0.8
}

menu_album_layout() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Organización de álbumes ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} Todos los temas del álbum en UNA sola carpeta"
    echo -e "  ${CYAN}2)${NC} UNA carpeta separada por álbum"
    echo -e "  ${CYAN}0)${NC} Volver"
    echo ""
    local a=""
    read -rp "  Opción [1/2] (Enter=1): " a || return 0
    [[ -z "$a" ]] && a="1"
    case "$a" in
        1) ALBUM_LAYOUT="single" ;;
        2) ALBUM_LAYOUT="split" ;;
        0) return 0 ;;
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1; return 0 ;;
    esac
    echo -e "  ${GREEN}✔ $(album_layout_label)${NC}"
    sleep 0.8
}

menu_cover() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Portada manual ══${NC}\n"
    echo -e "  Deja vacío para volver a AUTO (thumbnail de yt-dlp)."
    echo -e "  Acepta JPG y PNG; la ruta puede contener espacios."
    echo ""
    local raw="" path="" ext=""
    read -rp "  Ruta JPG/PNG: " raw || return 0
    raw="$(trim_input "$(strip_wrapping_quotes "$raw")")"
    raw="$(expand_tilde "$raw")"
    if [[ -z "$raw" ]]; then
        COVER_LOCAL=""
        echo -e "  ${GREEN}Portada: AUTO${NC}"
        sleep 0.8
        return 0
    fi
    if [[ ! -f "$raw" ]]; then
        echo -e "  ${RED}Archivo no encontrado. Sin cambios.${NC}"
        sleep 1.2
        return 0
    fi
    path="$(resolve_path "$raw")"
    ext="${path,,}"
    ext="${ext##*.}"
    if [[ "$ext" != "jpg" && "$ext" != "jpeg" && "$ext" != "png" ]]; then
        echo -e "  ${RED}✘ La portada debe ser JPG o PNG.${NC}"
        sleep 1.2
        return 0
    fi
    COVER_LOCAL="$path"
    printf '  %s✔ Portada: %s%s\n' "$GREEN" "$COVER_LOCAL" "$NC"
    sleep 0.8
}

menu_cookies() {
    while true; do
        clear_screen
        echo -e "${BOLD}${CYAN}══ Configuración de Cookies (YouTube / Anti-Bot) ══${NC}\n"
        echo -e "  Usa cookies si YouTube bloquea descargas pidiendo iniciar sesión."
        echo ""
        if [[ -n "$COOKIES_FILE" ]]; then
            printf '  %sActual:%s Archivo → %s\n\n' "$BOLD" "$NC" "$COOKIES_FILE"
        elif [[ -n "$COOKIES_BROWSER" ]]; then
            printf '  %sActual:%s Navegador → %s\n\n' "$BOLD" "$NC" "$COOKIES_BROWSER"
        else
            printf '  %sActual:%s Desactivadas\n\n' "$BOLD" "$NC"
        fi
        echo -e "  ${CYAN}1)${NC} Extraer cookies de un navegador (firefox, chromium, brave, chrome...)"
        echo -e "  ${CYAN}2)${NC} Usar archivo cookies.txt (formato Netscape)"
        echo -e "  ${CYAN}3)${NC} Desactivar cookies"
        echo -e "  ${CYAN}0)${NC} Volver"
        echo ""
        local c="" nav="" raw="" path=""
        read -rp "  Opción: " c || return 0
        case "$c" in
            1)
                read -rp "  Navegador (ej. firefox, brave, chromium, chrome): " nav || return 0
                nav="$(trim_input "$nav")"
                if [[ -n "$nav" && "$nav" =~ ^[a-zA-Z0-9_.:+-]+$ ]]; then
                    COOKIES_BROWSER="$nav"
                    COOKIES_FILE=""
                    msg_ok "Cookies desde navegador: $COOKIES_BROWSER"
                else
                    msg_err "Nombre de navegador inválido."
                fi
                sleep 1
                ;;
            2)
                read -rp "  Ruta al archivo cookies.txt: " raw || return 0
                raw="$(trim_input "$(strip_wrapping_quotes "$raw")")"
                raw="$(expand_tilde "$raw")"
                if [[ -n "$raw" && -f "$raw" && -r "$raw" ]]; then
                    path="$(resolve_path "$raw")"
                    COOKIES_FILE="$path"
                    COOKIES_BROWSER=""
                    msg_ok "Archivo de cookies: $COOKIES_FILE"
                else
                    msg_err "Archivo no encontrado o sin permisos de lectura."
                fi
                sleep 1.2
                ;;
            3)
                COOKIES_BROWSER=""
                COOKIES_FILE=""
                msg_ok "Cookies desactivadas."
                sleep 0.8
                ;;
            0|"") return 0 ;;
            *) msg_err "Opción inválida."; sleep 1 ;;
        esac
    done
}

toggle_var() {
    local name="${1-}"
    if [[ "${!name}" == "true" ]]; then printf -v "$name" '%s' false; else printf -v "$name" '%s' true; fi
}

menu_lyrics() {
    while true; do
        clear_screen
        refresh_syncedlyrics_state
        local state mode_label multi
        if [[ "$FETCH_LYRICS" == "true" ]]; then
            state="${GREEN}ACTIVO${NC}"
        else
            state="${RED}INACTIVO${NC}"
        fi
        if [[ "$LYRICS_MODE" == "embed" ]]; then
            mode_label="Incrustar en tags (USLT/SYLT, FLAC, M4A)"
        else
            mode_label="Archivo .lrc/.txt separado"
        fi
        if [[ "$SYNCEDLYRICS_AVAILABLE" == "true" ]]; then
            multi="${GREEN}$(provider_label)${NC} (syncedlyrics disponible)"
        else
            multi="${CYAN}$(provider_label)${NC}"
        fi
        echo -e "${BOLD}${CYAN}══ Configuración de letras ══${NC}\n"
        echo -e "  OtterRip usa LRCLIB y Multi-Web (sin API key)."
        echo -e "  ${DIM}Toda letra se valida por contenido, título y artista antes de guardarse.${NC}"
        echo ""
        printf '  %sEstado:%s        %b\n' "$BOLD" "$NC" "$state"
        printf '  %sModo:%s          %s\n' "$BOLD" "$NC" "$mode_label"
        printf '  %sProveedor:%s     %b\n' "$BOLD" "$NC" "$multi"
        printf '  %sServidor API:%s  %s%s%s\n' "$BOLD" "$NC" "$MAGENTA" "$LRCLIB_HOST" "$NC"
        printf '  %sIdioma:%s        %s\n' "$BOLD" "$NC" "${LYRICS_LANG:-cualquiera}"
        printf '  %sSincronizadas:%s %s   %sSustituir plana:%s %s\n' "$BOLD" "$NC" \
            "$([[ "$LYRICS_PREFER_SYNCED" == "true" ]] && echo prioritarias || echo 'sin prioridad')" \
            "$BOLD" "$NC" "$([[ "$LYRICS_UPGRADE_PLAIN" == "true" ]] && echo sí || echo no)"
        local syncLabel=""
        case "$LYRICS_SYNC_LEVEL" in
            line) syncLabel="línea" ;;
            word) syncLabel="palabra (exp.)" ;;
            auto) syncLabel="auto" ;;
        esac
        printf '  %sNivel sync:%s    %s   %sSidecar .lrc:%s %s\n' \
            "$BOLD" "$NC" "$syncLabel" "$BOLD" "$NC" \
            "$([[ "$LYRICS_SIDECAR" == "true" ]] && echo sí || echo no)"
        printf '  %sConfianza mín:%s %s   %sDesfase ms:%s  %s\n' \
            "$BOLD" "$NC" "$(( LYRICS_MIN_CONFIDENCE / 10 ))%" "$BOLD" "$NC" "${LYRICS_OFFSET_MS}ms"
        echo ""
        echo -e "  ${CYAN}1)${NC} Activar / desactivar búsqueda de letras"
        echo -e "  ${CYAN}2)${NC} Modo: incrustar en el archivo de audio"
        echo -e "  ${CYAN}3)${NC} Modo: guardar .lrc / .txt separado"
        echo -e "  ${CYAN}4)${NC} Servidor oficial (https://lrclib.net)"
        echo -e "  ${CYAN}5)${NC} Servidor local (http://localhost:3300)"
        echo -e "  ${CYAN}6)${NC} URL de servidor personalizada"
        echo -e "  ${CYAN}7)${NC} Verificar / reinstalar syncedlyrics (Multi-Web)"
        echo -e "  ${CYAN}8)${NC} Idioma preferido (es, en, pt, fr, it, de; vacío = cualquiera)"
        echo -e "  ${CYAN}9)${NC} Proveedores (LRCLIB / Multi-Web / ambos)"
        echo -e "  ${CYAN}10)${NC} Priorizar letras sincronizadas (sí/no)"
        echo -e "  ${CYAN}11)${NC} Sustituir letra plana incrustada por una sincronizada (sí/no)"
        echo -e "  ${CYAN}12)${NC} Nivel de sincronización (línea/palabra/auto)"
        echo -e "  ${CYAN}13)${NC} Generar sidecar .lrc junto al audio (sí/no)"
        echo -e "  ${CYAN}14)${NC} Confianza mínima (0-100 %, < esto = advertencia)"
        echo -e "  ${CYAN}15)${NC} Desfase global en ms (+ retrasa, - adelanta)"
        echo -e "  ${CYAN}0)${NC} Volver"
        echo ""
        local l=""
        read -rp "  Opción: " l || return 0
        case "$l" in
            1)
                if [[ "$FETCH_LYRICS" == "true" ]]; then
                    FETCH_LYRICS=false
                    echo -e "  ${YELLOW}Búsqueda de letras DESACTIVADA.${NC}"
                else
                    FETCH_LYRICS=true
                    echo -e "  ${GREEN}Búsqueda de letras ACTIVADA.${NC}"
                    if [[ "$SYNCEDLYRICS_AVAILABLE" != "true" ]]; then
                        echo -e "  ${DIM}Multi-Web requiere reinstalar las dependencias (opción 7).${NC}"
                    fi
                fi
                sleep 1
                ;;
            2) LYRICS_MODE="embed"; echo -e "  ${GREEN}Modo: incrustar en tags.${NC}"; sleep 0.8 ;;
            3) LYRICS_MODE="file";  echo -e "  ${GREEN}Modo: archivo .lrc/.txt.${NC}"; sleep 0.8 ;;
            4) LRCLIB_HOST="https://lrclib.net"; echo -e "  ${GREEN}Servidor oficial configurado.${NC}"; sleep 0.8 ;;
            5) LRCLIB_HOST="http://localhost:3300"; echo -e "  ${GREEN}Servidor local configurado.${NC}"; sleep 0.8 ;;
            6)
                local custom="" normalized=""
                read -rp "  URL del servidor API (ej. http://127.0.0.1:3300): " custom || return 0
                custom="$(strip_wrapping_quotes "$custom")"
                if normalized="$(normalize_host "$custom")"; then
                    LRCLIB_HOST="$normalized"
                    echo -e "  ${GREEN}Servidor: $LRCLIB_HOST${NC}"
                else
                    echo -e "  ${RED}✘ URL inválida. Debe comenzar con http:// o https://${NC}"
                fi
                sleep 1
                ;;
            7)
                if [[ "$SYNCEDLYRICS_AVAILABLE" == "true" ]]; then
                    echo -e "  ${GREEN}✔ syncedlyrics ya está instalado.${NC}"
                    sleep 0.8
                elif confirm "¿Instalar dependencias de letras en el entorno aislado de OtterRip?"; then
                    echo -e "  ${YELLOW}⚙${NC} Instalando dependencias de letras..."
                    if [[ "$PYBIN" != "$VENV_DIR/bin/python" ]]; then
                        if create_venv; then
                            PYBIN="$VENV_DIR/bin/python"
                            venv_pip_install -r "$REQUIREMENTS_FILE" || true
                        fi
                    else
                        venv_pip_install -r "$REQUIREMENTS_FILE" || true
                    fi
                    refresh_syncedlyrics_state
                    if [[ "$SYNCEDLYRICS_AVAILABLE" == "true" ]]; then
                        echo -e "  ${GREEN}✔ Multi-Web activo.${NC}"
                    else
                        echo -e "  ${RED}✘ No se pudo importar syncedlyrics.${NC}"
                    fi
                    sleep 1.5
                else
                    echo -e "  ${DIM}Omitido.${NC}"; sleep 0.6
                fi
                ;;
            8)
                local lang=""
                read -rp "  Idioma (código de 2 letras, Enter=cualquiera): " lang || return 0
                lang="${lang,,}"
                if [[ "$lang" =~ ^([a-z]{2})?$ ]]; then
                    LYRICS_LANG="$lang"
                    echo -e "  ${GREEN}Idioma: ${LYRICS_LANG:-cualquiera}${NC}"
                else
                    echo -e "  ${RED}✘ Código inválido.${NC}"
                fi
                sleep 1
                ;;
            9)
                echo -e "  ${CYAN}1)${NC} Solo LRCLIB   ${CYAN}2)${NC} LRCLIB + Multi-Web   ${CYAN}3)${NC} Solo Multi-Web"
                local pv=""
                read -rp "  Opción: " pv || return 0
                case "$pv" in
                    1) LYRICS_PROVIDERS="lrclib" ;;
                    2) LYRICS_PROVIDERS="lrclib,multiweb" ;;
                    3) LYRICS_PROVIDERS="multiweb" ;;
                    *) echo -e "  ${RED}Inválido.${NC}" ;;
                esac
                sleep 0.8
                ;;
            10) toggle_var LYRICS_PREFER_SYNCED; echo -e "  Priorizar sincronizadas: $LYRICS_PREFER_SYNCED"; sleep 0.8 ;;
            11) toggle_var LYRICS_UPGRADE_PLAIN; echo -e "  Sustituir plana por sincronizada: $LYRICS_UPGRADE_PLAIN"; sleep 0.8 ;;
            12)
                echo -e "  ${CYAN}1)${NC} Línea (por defecto)   ${CYAN}2)${NC} Palabra (experimental)   ${CYAN}3)${NC} Auto"
                local sl=""
                read -rp "  Opción: " sl || true
                case "$sl" in
                    1) LYRICS_SYNC_LEVEL="line" ;;
                    2) LYRICS_SYNC_LEVEL="word"; echo -e "  ${YELLOW}⚠ Palabra a palabra: experimental. Requiere dependencias adicionales.${NC}" ;;
                    3) LYRICS_SYNC_LEVEL="auto" ;;
                    *) echo -e "  ${RED}Inválido.${NC}" ;;
                esac
                sleep 0.8
                ;;
            13) toggle_var LYRICS_SIDECAR; echo -e "  Sidecar .lrc: $LYRICS_SIDECAR"; sleep 0.8 ;;
            14)
                local cv=""
                read -rp "  Confianza mínima (0-100, Enter=60): " cv || true
                if [[ "$cv" =~ ^[0-9]+$ ]] && (( cv >= 0 && cv <= 100 )); then
                    LYRICS_MIN_CONFIDENCE=$(( cv * 10 ))
                    echo -e "  ${GREEN}Confianza mínima: ${cv}%${NC}"
                elif [[ -z "$cv" ]]; then
                    LYRICS_MIN_CONFIDENCE=600
                    echo -e "  ${GREEN}Confianza mínima: 60%${NC}"
                else
                    echo -e "  ${RED}✘ Valor inválido (0-100).${NC}"
                fi
                sleep 0.8
                ;;
            15)
                local ov=""
                read -rp "  Desfase en ms (+ retrasa, - adelanta, Enter=0): " ov || true
                if [[ "$ov" =~ ^[+-]?[0-9]+$ ]]; then
                    LYRICS_OFFSET_MS="$ov"
                    echo -e "  ${GREEN}Desfase: ${ov}ms${NC}"
                elif [[ -z "$ov" ]]; then
                    LYRICS_OFFSET_MS=0
                    echo -e "  ${GREEN}Desfase: 0ms${NC}"
                else
                    echo -e "  ${RED}✘ Valor inválido.${NC}"
                fi
                sleep 0.8
                ;;
            0|"") return 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
}

menu_guide() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Guía de calidad ══${NC}\n"
    echo -e "  ${BOLD}MP3 320 kbps${NC}       — Muy compatible."
    echo -e "  ${BOLD}M4A AAC 256/320${NC}    — Buena relación calidad/tamaño."
    echo -e "  ${BOLD}FLAC 16/44.1${NC}       — Lossless tipo CD."
    echo -e "  ${BOLD}FLAC 24/96${NC}         — Hi-Res lossless."
    echo ""
    echo -e "  ${YELLOW}⚠${NC} La calidad real está limitada por la fuente."
    echo -e "  ${YELLOW}⚠${NC} FLAC desde una fuente con pérdida (YouTube) NO mejora la calidad."
    echo -e "  ${YELLOW}ℹ${NC} Las letras se obtienen de LRCLIB (gratis, sin key) y se validan."
    echo -e "  ${YELLOW}ℹ${NC} LRCLIB y Multi-Web se consultan en cascada según la configuración."
    echo -e "  ${YELLOW}ℹ${NC} Workers > 1 paralelizan playlists, álbumes y postproceso de letras."
    echo ""
    read -rp "  Enter para volver..." _ || true
}

# ═══════════════════════════════════════════════════════════════
#  MOTOR DE DESCARGA
# ═══════════════════════════════════════════════════════════════
draw_download_progress() {
    local done="${1:-0}" total="${2:-0}" ok="${3:-0}" err="${4:-0}" skip="${5:-0}" jobs="${6:-1}"
    local width=26 filled=0 i bar=""
    # Todos los contadores deben ser enteros no negativos: se descartan los
    # valores corruptos en lugar de provocar una división por cero.
    done="${done:-0}"; total="${total:-0}"; ok="${ok:-0}"
    err="${err:-0}"; skip="${skip:-0}"; jobs="${jobs:-1}"
    is_nonneg_int "$done" || done=0
    is_nonneg_int "$total" || total=0
    is_nonneg_int "$ok" || ok=0
    is_nonneg_int "$err" || err=0
    is_nonneg_int "$skip" || skip=0
    is_nonneg_int "$jobs" || jobs=1
    (( total <= 0 )) && return 0
    (( done > total )) && done=$total
    filled=$(( done * width / total ))
    (( filled > width )) && filled=$width
    (( filled < 0 )) && filled=0
    for ((i = 0; i < width; i++)); do
        if (( i < filled )); then bar+="█"; else bar+="░"; fi
    done
    [[ -t 1 ]] || return 0
    printf '\r\033[K  [%s] %d/%d | OK:%d ERR:%d SKIP:%d | %d worker(s)' \
        "$bar" "$done" "$total" "$ok" "$err" "$skip" "$jobs"
}

# Worker de descarga en paralelo: procesa un tramo de la colección.
_parallel_worker() {
    local start="${1-}" end="${2-}" url="${3-}" outtpl="${4-}" plflag="${5-}"
    local session="${6-}" log="${7-}"
    # Sin --no-warnings: los avisos de postproceso deben quedar en el log.
    local -a cmd=(
        ytdlp
        "${COMMON_FLAGS[@]}"
        "$plflag"
        --playlist-items "$start-$end"
        --no-progress
        --print-to-file "$SESSION_PRINT_TEMPLATE" "$session"
        -o "$outtpl"
        --
        "$url"
    )
    "${cmd[@]}" >"$log" 2>&1
}

# @brief Descarga una URL de forma secuencial mostrando el progreso.
# @param $1 URL a descargar.
# @param $2 Plantilla de salida.
# @param $3 Flag de playlist.
# @param $4 Manifiesto de sesión donde yt-dlp escribe las pistas.
# @param $5 Archivo de log.
# @return 0 si yt-dlp terminó bien; 1 si falló (o se canceló).
# @side-effects Descarga archivos y escribe el log.
run_sequential_download() {
    local url="${1-}" outtpl="${2-}" plflag="${3-}" session="${4-}" log="${5-}"
    local rc=0
    local -a cmd=(
        ytdlp
        "${COMMON_FLAGS[@]}"
        "$plflag"
        --print-to-file "$SESSION_PRINT_TEMPLATE" "$session"
        --progress
        --newline
        -o "$outtpl"
        --
        "$url"
    )
    # En segundo plano + `wait`: así SIGTERM/SIGHUP al script interrumpen la espera
    # y el trap de limpieza mata a yt-dlp (en primer plano bash retrasaría el trap
    # hasta que yt-dlp terminara). PIPESTATUS[0] conserva el estado de yt-dlp
    # (no el de tee) y se propaga como código de salida del subproceso.
    ( "${cmd[@]}" 2>&1 | tee "$log"; exit "${PIPESTATUS[0]:-0}" ) &
    local pid=$!
    CHILD_PIDS+=("$pid")
    wait "$pid" || rc=$?
    CHILD_PIDS=()
    return "$rc"
}

# Variante para la cola: segundo plano + sondeo del marcador de cancelación.
# @brief Descarga secuencial vigilando la marca de cancelación de la cola.
# @param $1 URL a descargar.
# @param $2 Plantilla de salida.
# @param $3 Flag de playlist.
# @param $4 Manifiesto de sesión.
# @param $5 Archivo de log.
# @return 0 si yt-dlp terminó bien; 1 si falló o se canceló.
# @side-effects Descarga archivos y escribe el log.
run_sequential_polled() {
    local url="${1-}" outtpl="${2-}" plflag="${3-}" session="${4-}" log="${5-}"
    local rc=0 pid=""
    local -a cmd=(
        ytdlp
        "${COMMON_FLAGS[@]}"
        "$plflag"
        --print-to-file "$SESSION_PRINT_TEMPLATE" "$session"
        --no-progress
        -o "$outtpl"
        --
        "$url"
    )
    "${cmd[@]}" >"$log" 2>&1 &
    pid=$!
    CHILD_PIDS+=("$pid")
    while kill -0 "$pid" 2>/dev/null; do
        if queue_cancelled; then
            kill_tree "$pid" TERM
            JOB_CANCELLED=true
            break
        fi
        sleep 1
    done
    wait "$pid" 2>/dev/null || rc=$?
    CHILD_PIDS=()
    return "$rc"
}

# @brief Descarga una colección repartiéndola entre varios workers.
# @param $1 URL de la colección.
# @param $2 Plantilla de salida.
# @param $3 Flag de playlist (--yes-playlist|--no-playlist).
# @param $4 Directorio de trabajo de la sesión.
# @param $5 Número total de elementos.
# @return 0 siempre; los resultados quedan en PARALLEL_OK/ERRORS/SKIPPED.
# @side-effects Descarga archivos y escribe los logs de cada worker.
run_parallel_download() {
    local url="${1-}" outtpl="${2-}" plflag="${3-}" workdir="${4-}" total="${5:-0}"
    local jobs="$PARALLEL_JOBS"
    (( jobs > total )) && jobs=$total
    (( jobs < 1 )) && jobs=1
    local base=$(( total / jobs )) rem=$(( total % jobs )) cursor=1 count
    local -a worker_pids=() worker_sessions=() worker_logs=()
    local i start end pid sf log

    for ((i = 1; i <= jobs; i++)); do
        # Reparte el resto entre los primeros workers (4 pistas/3 workers = 2+1+1).
        count=$(( base + (i <= rem ? 1 : 0) ))
        (( count <= 0 )) && continue
        start=$cursor
        end=$(( cursor + count - 1 ))
        cursor=$(( end + 1 ))
        sf="$workdir/session.$i"
        log="$workdir/worker.$i.log"
        _parallel_worker "$start" "$end" "$url" "$outtpl" "$plflag" "$sf" "$log" &
        pid=$!
        worker_pids+=("$pid")
        worker_sessions+=("$sf")
        worker_logs+=("$log")
        CHILD_PIDS+=("$pid")
    done

    if ((${#worker_pids[@]} == 0)); then
        PARALLEL_OK=0
        PARALLEL_ERRORS=1
        PARALLEL_SKIPPED=0
        return 1
    fi

    local ok=0 errors=0 skipped=0 finished=0 alive=0
    local prev_logs_size=0
    while true; do
        ok=0
        for sf in "${worker_sessions[@]}"; do
            ok=$((ok + $(count_lines "$sf")))
        done
        # Optimización: evitar recount (grep/sort -u) sobre todos los logs en
        # cada iteración si su tamaño total no ha cambiado desde el anterior.
        # El tamaño total se calcula con UNA sola llamada a wc (no un stat por log).
        local cur_logs_size=0
        cur_logs_size="$(logs_total_size "${worker_logs[@]}")"
        if (( cur_logs_size != prev_logs_size )); then
            errors="$(count_log_pattern '^ERROR:' "${worker_logs[@]}")"
            skipped="$(count_log_pattern "$SKIP_PATTERN" "${worker_logs[@]}")"
            prev_logs_size="$cur_logs_size"
        fi
        finished=$((ok + errors + skipped))
        (( finished > total )) && finished=$total
        draw_download_progress "$finished" "$total" "$ok" "$errors" "$skipped" "${#worker_pids[@]}"
        if queue_cancelled; then
            for pid in "${worker_pids[@]}"; do kill_tree "$pid" TERM; done
            JOB_CANCELLED=true
            break
        fi
        alive=0
        for pid in "${worker_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive=1
                break
            fi
        done
        (( alive == 0 )) && break
        sleep "$PROGRESS_POLL_INTERVAL"
    done
    [[ -t 1 ]] && printf '\n'

    local worker_fail=0
    for pid in "${worker_pids[@]}"; do
        if wait "$pid" 2>/dev/null; then
            :
        else
            worker_fail=$((worker_fail + 1))
        fi
    done
    CHILD_PIDS=()

    ok=0
    for sf in "${worker_sessions[@]}"; do
        ok=$((ok + $(count_lines "$sf")))
    done
    errors="$(count_log_pattern '^ERROR:' "${worker_logs[@]}")"
    skipped="$(count_log_pattern "$SKIP_PATTERN" "${worker_logs[@]}")"
    if (( worker_fail > 0 && errors == 0 )); then
        errors=$worker_fail
    fi

    PARALLEL_OK="$ok"
    PARALLEL_ERRORS="$errors"
    PARALLEL_SKIPPED="$skipped"
    show_last_errors 5 "${worker_logs[@]}"
    return 0
}

# @brief Construye las opciones comunes de yt-dlp para la sesión.
# @param $1 Archivo de historial de descargas (--download-archive).
# @return 0 siempre; deja el resultado en la matriz global COMMON_FLAGS.
# @side-effects Rellena COMMON_FLAGS.
build_common_flags() {
    local archive="${1-}"
    COMMON_FLAGS=(
        --ignore-errors
        --continue
        --no-overwrites
        --no-mtime
        --retries 10
        --fragment-retries 10
        --extractor-retries 5
        --retry-sleep "http:exp=1:20"
        --socket-timeout 30
        --sleep-requests 1
        --sleep-interval 1
        --max-sleep-interval 3
        -x
        --audio-format "$AFMT"
        --audio-quality "$AQ"
        --embed-metadata
        --parse-metadata "%(title)s:%(meta_title)s"
        --parse-metadata "%(artist,uploader,creator)s:%(meta_artist)s"
        --parse-metadata "%(album)s:%(meta_album)s"
        --parse-metadata "%(release_year,upload_date>%Y)s:%(meta_date)s"
        --parse-metadata "%(genre)s:%(meta_genre)s"
        --parse-metadata "%(track_number,playlist_index)s:%(meta_track)s"
        --match-filter "duration > $DUR_MIN & duration <= $DUR_MAX"
        --embed-thumbnail
        --convert-thumbnails jpg
        --download-archive "$archive"
    )
    if [[ "$FETCH_LYRICS" == "true" ]]; then
        COMMON_FLAGS+=( --write-subs --sub-langs "es,en" --sub-format "lrc/best" --convert-subs lrc )
    fi
    if [[ -n "$PP_VALUE" ]]; then
        COMMON_FLAGS+=( --postprocessor-args "$PP_VALUE" )
    fi
    if [[ "$AFMT" == "mp3" ]]; then
        COMMON_FLAGS+=( --postprocessor-args "ffmpeg:-id3v2_version 3 -write_id3v1 1" )
    fi
    if ((${#JS_RUNTIME_ARGS[@]})); then
        COMMON_FLAGS+=( "${JS_RUNTIME_ARGS[@]}" )
    fi
    # Cookies para evitar bloqueos de YouTube.
    if [[ -n "$COOKIES_FILE" && -r "$COOKIES_FILE" ]]; then
        COMMON_FLAGS+=( --cookies "$COOKIES_FILE" )
    elif [[ -n "$COOKIES_BROWSER" ]]; then
        COMMON_FLAGS+=( --cookies-from-browser "$COOKIES_BROWSER" )
    fi
    # Proxy (http/https/socks) para todas las peticiones de yt-dlp.
    if [[ -n "$OTTERRIP_PROXY" ]]; then
        COMMON_FLAGS+=( --proxy "$OTTERRIP_PROXY" )
    fi
}

# Resultados del último trabajo de descarga.
JOB_STATUS="ok"          # ok | partial | failed | cancelled
JOB_CLASS="ok"           # clasificación del peor error (ver errors.py)
JOB_CANCELLED=false
JOB_TOTAL=0; JOB_OK=0; JOB_ERRORS=0; JOB_SKIPPED=0; JOB_NEW=0
JOB_VERIFY_FAILED=0; JOB_COVER_MISSING=0; JOB_RETRIED=false; JOB_WARNINGS=0
JOB_LYR_FOUND=0; JOB_LYR_NOTFOUND=0; JOB_LYR_ALREADY=0
# shellcheck disable=SC2034  # los rellena run_parallel_download
PARALLEL_OK=0; PARALLEL_ERRORS=0; PARALLEL_SKIPPED=0

reset_job_state() {
    JOB_STATUS="ok"; JOB_CLASS="ok"; JOB_CANCELLED=false
    JOB_TOTAL=0; JOB_OK=0; JOB_ERRORS=0; JOB_SKIPPED=0; JOB_NEW=0
    JOB_VERIFY_FAILED=0; JOB_COVER_MISSING=0; JOB_RETRIED=false; JOB_WARNINGS=0
    JOB_LYR_FOUND=0; JOB_LYR_NOTFOUND=0; JOB_LYR_ALREADY=0
}

# Comprueba espacio libre en disco para el destino dado (en KB).
# Devuelve 0 si hay suficiente, 1 si hay muy poco y se debe abortar.
check_disk_space() {
    local dest="${1-}"
    local avail_kb
    avail_kb="$(df -Pk -- "$dest" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)"
    avail_kb="${avail_kb//[^0-9]/}"
    [[ -z "$avail_kb" ]] && avail_kb=0
    if (( avail_kb < MIN_DISK_SPACE_KB )); then
        msg_err "Espacio insuficiente en disco: ${avail_kb} KB libres en $dest (se requieren al menos 20 MB)."
        return 1
    elif (( avail_kb < WARN_DISK_SPACE_KB )); then
        msg_warn "Poco espacio libre en disco: ${avail_kb} KB en $dest (menos de 100 MB)."
    fi
    return 0
}

status_label() {
    case "${1-}" in
        ok) printf 'ÉXITO' ;;
        partial) printf 'ÉXITO PARCIAL' ;;
        cancelled) printf 'CANCELADO' ;;
        *)
            case "${JOB_CLASS}" in
                missing_dependency) printf 'ERROR: DEPENDENCIA FALTANTE' ;;
                service_unavailable) printf 'ERROR: SERVICIO NO DISPONIBLE (recuperable)' ;;
                permanent) printf 'ERROR PERMANENTE' ;;
                *) printf 'ERROR RECUPERABLE (se puede reintentar)' ;;
            esac
            ;;
    esac
}

# Postproceso común: verificación real → metadatos → letras. Usa session.final.
# @brief Verifica la integridad y aplica metadatos y letras a lo descargado.
# @param $1 Manifiesto de sesión final (JSON Lines).
# @param $2 Carpeta de destino.
# @param $3 Archivo de historial de descargas.
# @param $4 Directorio de trabajo de la sesión.
# @return 0 siempre (los errores parciales se reflejan en las variables JOB_*).
# @side-effects Modifica los metadatos y las letras de los archivos descargados.
post_process_session() {
    local session_final="${1-}" dest="${2-}" archive="${3-}" workdir="${4-}"
    local verified="$workdir/session.verified" failed="$workdir/session.failed"
    local lyr_summary="$workdir/lyrics.summary" vrc=0

    JOB_NEW="$(count_lines "$session_final")"
    if (( JOB_NEW == 0 )); then
        echo -e "  ${GREEN}✔ No hay pistas nuevas que requieran procesamiento (ya descargadas).${NC}"
        return 0
    fi

    echo -e "  ${YELLOW}⚙${NC} Verificando integridad de ${JOB_NEW} archivo(s)..."
    # El backend espera un ID por línea: se normaliza el manifiesto de sesión.
    # Ojo: el nombre NO empieza por "session." para no confundirse con los
    # manifiestos de sesión (glob "session.*" del recovery).
    local ids_file="$workdir/manifest.ids"
    if ! session_to_ids "$session_final" "$ids_file"; then
        ids_file="$session_final"
    fi
    py_core verify-session --session "$ids_file" --ext "$AFMT" --dest "$dest" \
        --archive "$archive" --out-ok "$verified" --out-failed "$failed" || vrc=$?
    JOB_VERIFY_FAILED="$(count_lines "$failed")"
    JOB_OK="$(count_lines "$verified")"
    if [[ -z "$COVER_LOCAL" && -f "$verified" ]]; then
        JOB_COVER_MISSING="$( { grep -c '"cover_missing": true' "$verified" 2>/dev/null || true; } | head -n1)"
        [[ "$JOB_COVER_MISSING" =~ ^[0-9]+$ ]] || JOB_COVER_MISSING=0
    fi
    log_event INFO "verify-session rc=$vrc ok=$JOB_OK fallidas=$JOB_VERIFY_FAILED dest=$dest"
    (( JOB_OK == 0 )) && return 0

    echo -e "  ${YELLOW}⚙${NC} Reparando metadatos${COVER_LOCAL:+ y portada manual} de ${JOB_OK} pista(s)..."
    fix_metadata_batch "$dest" "$AFMT" "$verified" "$COVER_LOCAL" "$workdir" || true

    if [[ "$FETCH_LYRICS" == "true" ]]; then
        echo -e "  ${YELLOW}⚙${NC} Buscando e incrustando letras (${JOB_OK} en cola)..."
        process_lyrics "$dest" "$AFMT" "$verified" "$lyr_summary" || true
        JOB_LYR_FOUND="$(summary_value "$lyr_summary" found 0)"
        JOB_LYR_NOTFOUND="$(summary_value "$lyr_summary" notfound 0)"
        JOB_LYR_ALREADY="$(summary_value "$lyr_summary" already 0)"
    fi
    return 0
}

# Repara metadatos de un conjunto de pistas verificadas.
# Con más de 4 pistas y varios workers, el manifiesto de sesión se divide en
# lotes que se procesan en paralelo con xargs (el backend no necesita cambios:
# cada lote es un manifiesto de sesión independiente).
# @brief Repara metadatos en paralelo cuando hay suficientes pistas.
# @param $1 Carpeta de destino.
# @param $2 Formato de audio (mp3|m4a|flac).
# @param $3 Manifiesto de sesión verificado.
# @param $4 Portada manual (o vacío).
# @param $5 Directorio de trabajo.
# @return 0 siempre.
# @side-effects Escribe etiquetas en los archivos de audio indicados.
fix_metadata_batch() {
    local dest="${1-}" fmt="${2-}" session="${3-}" cover="${4-}" workdir="${5-}"
    local n_lines=0 jobs="$PARALLEL_JOBS" chunk_dir="" chunk_prefix=""
    n_lines="$(count_lines "$session")"
    (( n_lines > 4 && jobs > 1 )) || {
        fix_metadata "$dest" "$fmt" "$session" "$cover" || true
        return 0
    }
    command -v split >/dev/null 2>&1 || {
        fix_metadata "$dest" "$fmt" "$session" "$cover" || true
        return 0
    }
    chunk_dir="$workdir/mdchunks"
    mkdir -p -- "$chunk_dir" 2>/dev/null || {
        fix_metadata "$dest" "$fmt" "$session" "$cover" || true
        return 0
    }
    chunk_prefix="$chunk_dir/chunk."
    local per=$(( (n_lines + jobs - 1) / jobs ))
    (( per < 1 )) && per=1
    if ! split -l "$per" -d -- "$session" "$chunk_prefix" 2>/dev/null; then
        rm -rf -- "$chunk_dir"
        fix_metadata "$dest" "$fmt" "$session" "$cover" || true
        return 0
    fi
    local -a chunks=()
    local c=""
    for c in "$chunk_prefix"*; do
        [[ -f "$c" ]] && chunks+=("$c")
    done
    ((${#chunks[@]} > 1)) || {
        rm -rf -- "$chunk_dir"
        fix_metadata "$dest" "$fmt" "$session" "$cover" || true
        return 0
    }
    log_debug "fix-metadata en paralelo: ${#chunks[@]} lotes, $jobs workers"
    # El backend se invoca directamente (no es una función de Bash): el
    # subproceso necesita PYTHONPATH, PYBIN y la portada como argumentos.
    # shellcheck disable=SC2016  # el script interno se expande en tiempo de ejecución
    printf '%s\0' "${chunks[@]}" \
        | xargs -0 -P "$jobs" -I '{}' env \
            "PYTHONPATH=$LIB_DIR${PYTHONPATH:+:$PYTHONPATH}" \
            "PYTHONDONTWRITEBYTECODE=1" \
            "PYBIN=${PYBIN:-python3}" \
            bash -c '
                chunk="$1"; dest="$2"; fmt="$3"; cover="$4"
                if [[ -n "$cover" ]]; then
                    "${PYBIN:-python3}" -m otterrip_core fix-metadata "$dest" "$fmt" \
                        --session "$chunk" --cover "$cover"
                else
                    "${PYBIN:-python3}" -m otterrip_core fix-metadata "$dest" "$fmt" \
                        --session "$chunk"
                fi
            ' _ "{}" "$dest" "$fmt" "$cover" >/dev/null 2>&1 || true
    rm -rf -- "$chunk_dir"
    return 0
}

# Registra una descarga en el historial consultable (TSV).
# Columnas: timestamp, url, title, dest, status, duration_secs
# @brief Registra una descarga en el historial consultable.
# @param $1 URL descargada.
# @param $2 Título de la pista o colección.
# @param $3 Carpeta de destino.
# @param $4 Estado final (ok|partial|failed|cancelled).
# @param $5 Duración en segundos.
# @return 0 siempre.
# @side-effects Añade una línea a $OTTERRIP_DATA_DIR/history.tsv.
append_history() {
    local url="${1-}" title="${2-}" dest="${3-}" status="${4-}" duration="${5-0}"
    local file="$OTTERRIP_DATA_DIR/history.tsv" field=""
    [[ -d "$OTTERRIP_DATA_DIR" ]] || mkdir -p -- "$OTTERRIP_DATA_DIR" 2>/dev/null || return 0
    is_private_dir "$OTTERRIP_DATA_DIR" || return 0
    [[ "$duration" =~ ^[0-9]+$ ]] || duration=0
    # Los campos no pueden contener tabuladores ni saltos de línea.
    for field in "$url" "$title" "$dest" "$status"; do
        field="${field//$'\t'/ }"
        field="${field//$'\n'/ }"
    done
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$url" "$title" "$dest" "$status" "$duration" \
        >> "$file" 2>/dev/null || true
    return 0
}

# Muestra el historial de descargas (últimas entradas).
# @brief Muestra el historial de descargas.
# @param $1 Número máximo de entradas a mostrar (por defecto 50).
# @return 0 siempre.
# @side-effects Ninguno (solo lectura).
show_history() {
    local file="$OTTERRIP_DATA_DIR/history.tsv" max="${1:-50}" n=0 line=""
    if [[ ! -f "$file" ]]; then
        msg_info "Todavía no hay descargas registradas en el historial."
        return 0
    fi
    echo -e "${BOLD}${CYAN}══ Historial de descargas ══${NC}\n"
    printf '  %s%-19s %-28s %-10s %-8s %s%s\n' "$BOLD" "FECHA" "URL" "ESTADO" "DURACIÓN" "DESTINO" "$NC"
    while IFS=$'\t' read -r ts url title dest status dur; do
        [[ -z "${ts:-}" ]] && continue
        printf '  %-19s %-28s %-10s %-8s %s\n' \
            "${ts:0:19}" "${url:0:28}" "${status:0:10}" "${dur:-0}s" "${dest:0:60}"
        n=$((n + 1))
        (( n >= max )) && break
    done < "$file"
    echo ""
    printf '  %sTotal de entradas: %s%s\n' "$DIM" "$n" "$NC"
    return 0
}

# Envía una notificación de escritorio al terminar (si hay servidor de avisos).
# @brief Envía una notificación de escritorio al terminar una descarga.
# @param $1 Título de la notificación.
# @param $2 Cuerpo del mensaje.
# @return 0 siempre.
# @side-effects Puede lanzar notify-send, terminal-notifier u osascript.
notify_finish() {
    local title="${1-}" body="${2-}"
    [[ "${OTTERRIP_NOTIFY:-false}" == "true" ]] || return 0
    if command -v notify-send >/dev/null 2>&1; then
        notify-send --app-name="OtterRip" "$title" "$body" 2>/dev/null || true
    elif command -v terminal-notifier >/dev/null 2>&1; then
        terminal-notifier -title "OtterRip" -message "$body" 2>/dev/null || true
    elif command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"$body\" with title \"OtterRip\"" 2>/dev/null || true
    fi
    return 0
}

# Ejecuta una descarga completa (sin menús). Rellena JOB_*; devuelve 0 si
# el trabajo terminó (aunque sea parcial) y 1 si no pudo ni empezar.
# @brief Ejecuta una descarga completa y actualiza el estado del trabajo.
# @param $1 URL a descargar.
# @param $2 Tipo detectado (song|playlist|album).
# @param $3 Número de elementos detectado (o vacío).
# @param $4 Carpeta de destino.
# @return 0 si el trabajo terminó (aunque sea parcial); 1 si no pudo empezar.
# @side-effects Descarga archivos, registra el historial y libera el bloqueo.
download_job() {
    local rc=0 started="" finished="" title="${DETECTED_TITLE:-}" dest="${4-}"
    reset_job_state
    started="$(date +%s)"
    _download_job_body "$@" || rc=$?
    release_dest_lock
    finished="$(date +%s)"
    local duration=0
    is_nonneg_int "$finished" && is_nonneg_int "$started" && duration=$(( finished - started ))
    (( duration < 0 )) && duration=0
    if [[ "$DRY_RUN" != "true" ]]; then
        append_history "${1-}" "$title" "$dest" "$JOB_STATUS" "$duration"
        notify_finish "Descarga ${JOB_STATUS}" \
            "$(status_label "$JOB_STATUS"): $JOB_OK OK, $JOB_ERRORS error(es), $JOB_SKIPPED omitida(s)"
    fi
    return "$rc"
}

# @brief Cuerpo de la descarga: validación, flags, descarga y postproceso.
# @param $1 URL a descargar.
# @param $2 Tipo detectado (song|playlist|album).
# @param $3 Número de elementos detectado (o vacío).
# @param $4 Carpeta de destino.
# @return 0 si el trabajo terminó; 1 si no pudo ejecutarse.
# @side-effects Crea la sesión temporal, descarga y borra la sesión al terminar.
_download_job_body() {
    local url="${1-}" type_mode="${2-song}" detected_count="${3-}" dest="${4-}"
    local archive="$dest/.otterrip_archive" outtpl="" plflag=""

    # La URL debe ser http(s) y no puede empezar por "-" (interpretación de
    # flags por parte de yt-dlp).
    if ! is_safe_url "$url"; then
        msg_err "URL no válida o insegura: ${url}"
        JOB_STATUS="failed"; JOB_CLASS="permanent"
        return 1
    fi

    if ! mkdir -p -- "$dest"; then
        msg_err "No se pudo crear la carpeta de destino: $dest"
        JOB_STATUS="failed"; JOB_CLASS="permanent"
        return 1
    fi
    if [[ ! -w "$dest" ]]; then
        msg_err "Sin permiso de escritura en: $dest"
        JOB_STATUS="failed"; JOB_CLASS="permanent"
        return 1
    fi
    if ! check_disk_space "$dest"; then
        JOB_STATUS="failed"; JOB_CLASS="recoverable"
        return 1
    fi
    acquire_dest_lock "$dest" || { JOB_STATUS="failed"; JOB_CLASS="recoverable"; return 1; }
    clean_stale_temp "$dest"

    case "$type_mode" in
        song)
            plflag="--no-playlist"
            outtpl="$dest/%(title)s [%(id)s].%(ext)s"
            ;;
        playlist)
            plflag="--yes-playlist"
            outtpl="$dest/%(playlist_title)s/%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"
            ;;
        album)
            plflag="--yes-playlist"
            if [[ "$ALBUM_LAYOUT" == "single" ]]; then
                outtpl="$dest/%(title)s [%(id)s].%(ext)s"
            else
                outtpl="$dest/%(album,playlist_title)s/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"
            fi
            ;;
    esac
    build_common_flags "$archive"
    dump_state

    # ── Modo simulación: no se descarga nada ────────────────────
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${BOLD}${YELLOW}  ══ Simulación (--dry-run) ══${NC}"
        printf '  %sURL:%s     %s\n' "$BOLD" "$NC" "$url"
        printf '  %sTipo:%s    %s\n' "$BOLD" "$NC" "$type_mode"
        printf '  %sDestino:%s %s\n' "$BOLD" "$NC" "$dest"
        printf '  %sPlantilla:%s %s\n' "$BOLD" "$NC" "$outtpl"
        printf '  %sPerfil:%s  %s (%s)\n' "$BOLD" "$NC" "$PROFILE" "$AFMT/$AQ"
        printf '  %sDuración:%s >%ss y ≤ %ss\n' "$BOLD" "$NC" "$DUR_MIN" "$DUR_MAX"
        printf '  %sWorkers:%s  %s\n' "$BOLD" "$NC" "$PARALLEL_JOBS"
        printf '  %sLetras:%s  %s\n' "$BOLD" "$NC" "$([[ "$FETCH_LYRICS" == "true" ]] && echo activas || echo desactivadas)"
        [[ -n "$OTTERRIP_PROXY" ]] && printf '  %sProxy:%s   %s\n' "$BOLD" "$NC" "$OTTERRIP_PROXY"
        printf '  %sFlags:%s\n' "$BOLD" "$NC"
        local f=""
        for f in "${COMMON_FLAGS[@]}"; do
            printf '    %s\n' "$f"
        done
        JOB_STATUS="ok"
        JOB_TOTAL=1
        return 0
    fi

    local use_parallel="false" total="$detected_count"
    if [[ "$type_mode" == "song" ]]; then
        total=1
    elif (( PARALLEL_JOBS > 1 )); then
        if ! [[ "$total" =~ ^[0-9]+$ ]] || (( 10#$total <= 1 )); then
            echo -e "  ${YELLOW}⚙${NC} Contando elementos de la colección..."
            total="$(count_playlist_entries "$url")"
        fi
        if [[ "$total" =~ ^[0-9]+$ ]] && (( 10#$total > 1 )); then
            use_parallel="true"
        fi
    fi
    echo ""
    echo -e "${BOLD}${GREEN}  ══ Descargando ══${NC}"
    printf '  Perfil:   %s%s%s\n' "$BOLD" "$PROFILE" "$NC"
    printf '  Duración: %s>%ss y ≤ %ss%s\n' "$BOLD" "$DUR_MIN" "$DUR_MAX" "$NC"
    printf '  Destino:  %s%s%s\n' "$CYAN" "$dest" "$NC"
    if [[ "$use_parallel" == "true" ]]; then
        local effective_jobs="$PARALLEL_JOBS"
        (( effective_jobs > total )) && effective_jobs="$total"
        (( effective_jobs < 1 )) && effective_jobs=1
        printf '  Modo:     %sParalelo (%s workers, %s elementos)%s\n' \
            "$CYAN" "$effective_jobs" "$total" "$NC"
    else
        printf '  Modo:     %sSecuencial%s\n' "$CYAN" "$NC"
    fi
    if [[ "$FETCH_LYRICS" == "true" ]]; then
        printf '  Letras:   %sActivas [%s] (%s)%s\n' "$MAGENTA" "$LRCLIB_HOST" "$LYRICS_MODE" "$NC"
    fi
    if [[ "$type_mode" == "album" ]]; then
        printf '  Álbum:    %s%s%s\n' "$MAGENTA" "$(album_layout_label)" "$NC"
    fi
    echo ""

    # Sesión persistente: sobrevive a una interrupción y se recupera al reiniciar.
    WORK_DIR="$SESSIONS_DIR/$(date +%Y%m%d-%H%M%S)-$$"
    WORK_DONE=false
    if ! { mkdir -p -- "$WORK_DIR" && chmod 700 -- "$WORK_DIR"; }; then
        echo -e "  ${RED}✘ No se pudo crear el directorio de sesión.${NC}"
        WORK_DIR=""
        JOB_STATUS="failed"; JOB_CLASS="permanent"
        return 1
    fi
    {
        printf 'DEST=%s\n' "$dest"
        printf 'AFMT=%s\n' "$AFMT"
        printf 'URL=%s\n' "$url"
    } > "$WORK_DIR/meta.env"

    local download_rc=0 download_errors=0 download_skipped=0
    local -a all_sessions=() error_logs=() all_logs=()
    local f=""

    # ── Pasada 1 ───────────────────────────────────────────────
    if [[ "$use_parallel" == "true" ]]; then
        run_parallel_download "$url" "$outtpl" "$plflag" "$WORK_DIR" "$total" || download_rc=$?
        download_errors="$PARALLEL_ERRORS"
        download_skipped="$PARALLEL_SKIPPED"
        for f in "$WORK_DIR"/session.*; do [[ -f "$f" ]] && all_sessions+=("$f"); done
        for f in "$WORK_DIR"/worker.*.log; do [[ -f "$f" ]] && error_logs+=("$f"); done
    else
        local seq_session="$WORK_DIR/session.1" seq_log="$WORK_DIR/main.log"
        if [[ -n "$QUEUE_TASK_ID" ]]; then
            run_sequential_polled "$url" "$outtpl" "$plflag" "$seq_session" "$seq_log" || download_rc=$?
        else
            run_sequential_download "$url" "$outtpl" "$plflag" "$seq_session" "$seq_log" || download_rc=$?
        fi
        all_sessions=("$seq_session")
        error_logs=("$seq_log")
        download_errors="$(count_log_pattern '^ERROR:' "$seq_log")"
        download_skipped="$(count_log_pattern "$SKIP_PATTERN" "$seq_log")"
        (( download_errors > 0 )) && show_last_errors 5 "$seq_log"
    fi
    all_logs=("${error_logs[@]}")

    # ── Reintento acotado: solo errores recuperables / de servicio ──
    if [[ "$JOB_CANCELLED" != "true" ]] && (( download_errors > 0 && MAX_RETRY_PASSES > 0 )); then
        local recov=0 svc=0
        recov="$(class_value recoverable "${error_logs[@]}")"
        svc="$(class_value service_unavailable "${error_logs[@]}")"
        [[ "$recov" =~ ^[0-9]+$ ]] || recov=0
        [[ "$svc" =~ ^[0-9]+$ ]] || svc=0
        if (( recov + svc > 0 )); then
            JOB_RETRIED=true
            echo -e "  ${YELLOW}↻${NC} Reintentando una vez los elementos fallidos (los ya descargados se omiten)..."
            (( svc > 0 )) && sleep 10
            local retry_session="$WORK_DIR/session.retry" retry_log="$WORK_DIR/retry.log" retry_rc=0
            if [[ -n "$QUEUE_TASK_ID" ]]; then
                run_sequential_polled "$url" "$outtpl" "$plflag" "$retry_session" "$retry_log" || retry_rc=$?
            else
                run_sequential_download "$url" "$outtpl" "$plflag" "$retry_session" "$retry_log" || retry_rc=$?
            fi
            all_sessions+=("$retry_session")
            all_logs+=("$retry_log")
            # La pasada de reintento recorre toda la URL: sus errores sustituyen a los de la pasada 1.
            error_logs=("$retry_log")
            download_errors="$(count_log_pattern '^ERROR:' "$retry_log")"
            download_rc=$retry_rc
        fi
    fi

    (( download_rc != 0 && download_errors == 0 )) && download_errors=1
    JOB_ERRORS="$download_errors"
    JOB_SKIPPED="$download_skipped"
    if (( download_errors > 0 )); then
        JOB_CLASS="$(class_value overall "${error_logs[@]}")"
        [[ -n "$JOB_CLASS" ]] || JOB_CLASS="recoverable"
    fi

    local warns=0
    warns="$(count_log_pattern '^WARNING:.*(thumbnail|postprocess|metadata|subtitle)' "${all_logs[@]}")"

    local session_final="$WORK_DIR/session.final"
    if ! py_core merge-sessions "$session_final" "${all_sessions[@]}" >/dev/null; then
        echo -e "  ${RED}✘ No se pudo consolidar el archivo de sesión (se conserva en $WORK_DIR).${NC}"
        JOB_STATUS="failed"; JOB_CLASS="permanent"
        return 1
    fi

    post_process_session "$session_final" "$dest" "$archive" "$WORK_DIR"

    if ! [[ "$total" =~ ^[0-9]+$ ]] || (( 10#${total:-0} == 0 )); then
        total=$((JOB_OK + JOB_ERRORS + JOB_SKIPPED))
    fi
    (( total < JOB_OK )) && total="$JOB_OK"
    JOB_TOTAL="$total"

    # Estado final: nunca se confía solo en el código de salida de yt-dlp.
    if [[ "$JOB_CANCELLED" == "true" ]]; then
        JOB_STATUS="cancelled"
    elif (( JOB_OK == 0 && (JOB_ERRORS > 0 || JOB_VERIFY_FAILED > 0) )); then
        JOB_STATUS="failed"
        (( JOB_VERIFY_FAILED > 0 && JOB_ERRORS == 0 )) && JOB_CLASS="recoverable"
    elif (( JOB_ERRORS > 0 || JOB_VERIFY_FAILED > 0 || JOB_COVER_MISSING > 0 || warns > 0 )); then
        JOB_STATUS="partial"
    else
        JOB_STATUS="ok"
    fi
    JOB_WARNINGS="$warns"
    log_event INFO "job $JOB_STATUS url=$url ok=$JOB_OK err=$JOB_ERRORS verify_fail=$JOB_VERIFY_FAILED class=$JOB_CLASS"

    WORK_DONE=true
    if (( VERBOSITY >= 2 )); then
        printf '\n  Depuración: sesión conservada en %s\n' "$WORK_DIR" >&2
    else
        rm -rf -- "$WORK_DIR"
    fi
    WORK_DIR=""
    return 0
}

print_job_summary() {
    local dest="${1-}"
    echo ""
    echo -e "${BOLD}${GREEN}  ══ Resumen de la descarga ══${NC}"
    printf '  Total detectado:           %s\n' "$JOB_TOTAL"
    printf '  Descargados y verificados: %s\n' "$JOB_OK"
    printf '  Errores de descarga:       %s\n' "$JOB_ERRORS"
    printf '  Defectuosos (reintentables): %s\n' "$JOB_VERIFY_FAILED"
    printf '  Ya existentes/saltados:    %s\n' "$JOB_SKIPPED"
    printf '  Archivos nuevos:           %s\n' "$JOB_NEW"
    (( JOB_COVER_MISSING > 0 )) && printf '  Sin portada incrustada:    %s\n' "$JOB_COVER_MISSING"
    (( JOB_WARNINGS > 0 )) && printf '  Avisos de postproceso:     %s (ver %s)\n' "$JOB_WARNINGS" "$LOG_FILE"
    [[ "$JOB_RETRIED" == "true" ]] && printf '  Reintento automático:      realizado\n'
    if [[ "$FETCH_LYRICS" == "true" ]]; then
        printf '  Letras encontradas:        %s\n' "$JOB_LYR_FOUND"
        printf '  Letras no encontradas:     %s\n' "$JOB_LYR_NOTFOUND"
        printf '  Letras ya existentes:      %s\n' "$JOB_LYR_ALREADY"
    else
        printf '  Letras:                    desactivadas\n'
    fi
    echo ""
    case "$JOB_STATUS" in
        ok) echo -e "${GREEN}${BOLD}  ✔ Estado: $(status_label ok) — proceso finalizado correctamente.${NC}" ;;
        partial) echo -e "${YELLOW}${BOLD}  ⚠ Estado: $(status_label partial) — hay avisos/errores parciales.${NC}" ;;
        cancelled) echo -e "${YELLOW}${BOLD}  ■ Estado: $(status_label cancelled).${NC}" ;;
        *) echo -e "${RED}${BOLD}  ✘ Estado: $(status_label failed).${NC}" ;;
    esac
    printf '  Archivos en: %s%s%s\n' "$CYAN" "$dest" "$NC"
}

menu_download() {
    local seguir="true"
    while [[ "$seguir" == "true" ]]; do
        clear_screen
        echo -e "${BOLD}${CYAN}══ Nueva descarga ══${NC}\n"
        echo -e "  Pega la URL; OtterRip detecta automáticamente si es"
        echo -e "  ${BOLD}canción, playlist o álbum${NC}."
        echo ""
        local url=""
        read -rp "  URL (Enter para volver): " url || return 0
        url="$(trim_input "$(strip_wrapping_quotes "$url")")"
        [[ -z "$url" ]] && return 0
        if ! is_http_url "$url"; then
            echo -e "  ${RED}✘ URL inválida. Debe comenzar con http:// o https://${NC}"
            sleep 1.5
            continue
        fi

        echo ""
        echo -e "  ${YELLOW}🔎 Analizando URL...${NC}"
        detect_url_type "$url"
        local type_mode="$DETECTED_TYPE"
        local detected_count="$DETECTED_COUNT"
        local detected_title="$DETECTED_TITLE"

        echo -e "  ${GREEN}✔ Detectado:${NC} ${BOLD}$(detected_type_label "$type_mode")${NC}"
        if [[ -n "$detected_title" ]]; then
            printf '  %sNombre:%s %s\n' "$BOLD" "$NC" "$detected_title"
        fi
        if [[ -n "$detected_count" ]]; then
            printf '  %sElementos:%s %s\n' "$BOLD" "$NC" "$detected_count"
        fi
        if [[ "$DETECTION_FALLBACK" == "true" ]]; then
            echo -e "  ${YELLOW}⚠${NC} No se pudo consultar yt-dlp; se usó detección por URL."
        fi
        echo ""

        printf '  %sRuta de guardado actual:%s %s\n' "$BOLD" "$NC" "$(destination_display)"
        read -rp "  Nueva carpeta o ruta (Enter=mantener actual): " folder_choice || return 0
        if [[ -n "$folder_choice" ]]; then
            local resolved_dest=""
            if resolved_dest="$(resolve_destination "$folder_choice")"; then
                LAST_DOWNLOAD_FOLDER="$resolved_dest"
            else
                echo -e "  ${RED}✘ Ruta inválida (no se permite '..' en rutas relativas).${NC}"
                sleep 1.5
                continue
            fi
        fi

        local DEST=""
        DEST="$(destination_display)"
        download_job "$url" "$type_mode" "$detected_count" "$DEST" || true
        print_job_summary "$DEST"

        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar más (misma ruta)"
        echo -e "  ${CYAN}2)${NC} Descargar más en OTRA ruta"
        echo -e "  ${CYAN}3)${NC} Volver al menú principal"
        echo -e "  ${CYAN}4)${NC} Salir"
        echo ""
        local post_op=""
        read -rp "  Opción (Enter=1): " post_op || return 0
        [[ -z "$post_op" ]] && post_op="1"
        case "$post_op" in
            1) seguir="true" ;;
            2)
                seguir="true"
                printf '  %sRuta actual:%s %s\n' "$BOLD" "$NC" "$DEST"
                local new_dest="" resolved_new=""
                read -rp "  Nueva carpeta o ruta (Enter=mantener actual): " new_dest || return 0
                if [[ -n "$new_dest" ]]; then
                    if resolved_new="$(resolve_destination "$new_dest")"; then
                        LAST_DOWNLOAD_FOLDER="$resolved_new"
                    else
                        echo -e "  ${RED}Ruta inválida. Se mantiene la actual.${NC}"
                        sleep 1.2
                    fi
                fi
                ;;
            3) seguir="false" ;;
            4) save_config; echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
            *) seguir="false" ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  RECUPERACIÓN DE SESIONES INTERRUMPIDAS
# ═══════════════════════════════════════════════════════════════
recover_sessions() {
    local d="" f="" n=0 dest="" fmt="" ans="" found=false merged=""
    [[ -d "$SESSIONS_DIR" ]] || return 0
    for d in "$SESSIONS_DIR"/*/; do
        [[ -d "$d" ]] || continue
        d="${d%/}"
        # Sesiones vacías y antiguas (>14 días) se eliminan; nunca las recientes.
        if ! session_dir_has_entries "$d"; then
            if [[ -n "$(find "$d" -maxdepth 0 -mtime +$SESSION_EXPIRY_DAYS 2>/dev/null)" ]]; then
                rm -rf -- "$d"
            fi
            continue
        fi
        # Una sesión de otra instancia viva (nombre termina en -PID) no se toca.
        local pid="${d##*-}"
        if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" != "$$" ]] && kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        found=true
        merged="$d/session.final"
        py_core merge-sessions "$merged" "$d"/session.[0-9]* "$d"/session.retry >/dev/null 2>&1 || true
        n="$(count_lines "$merged")"
        dest="$(sed -n 's/^DEST=//p' "$d/meta.env" 2>/dev/null | head -n1)"
        fmt="$(sed -n 's/^AFMT=//p' "$d/meta.env" 2>/dev/null | head -n1)"
        case "$fmt" in mp3|flac|m4a) ;; *) fmt="$AFMT" ;; esac
        echo -e "  ${YELLOW}⚠${NC} Sesión interrumpida: ${n} pista(s) sin postprocesar → ${dest:-?}"
        if [[ -z "$dest" || ! -d "$dest" ]]; then
            echo -e "    ${DIM}El destino ya no existe; se conserva la sesión en $d${NC}"
            continue
        fi
        if [[ ! -t 0 ]]; then
            log_event WARN "sesión pendiente (no interactivo): $d"
            continue
        fi
        read -rp "    ¿Verificar y completar metadatos/letras ahora? [S/n/d=descartar]: " ans || return 0
        case "${ans,,}" in
            d) rm -rf -- "$d"; echo "    Sesión descartada." ;;
            n) echo "    Se conserva para más tarde." ;;
            *)
                local saved_fmt="$AFMT"
                AFMT="$fmt"
                WORK_DIR=""
                post_process_session "$merged" "$dest" "$dest/.otterrip_archive" "$d"
                AFMT="$saved_fmt"
                rm -rf -- "$d"
                log_event INFO "sesión recuperada: $dest"
                ;;
        esac
    done
    [[ "$found" == "true" ]] && sleep 1
    # Archivos temporales huérfanos de ejecuciones anteriores en el destino por defecto
    clean_stale_temp "$(destination_display)"
    return 0
}

# ═══════════════════════════════════════════════════════════════
#  COLA DE DESCARGAS
# ═══════════════════════════════════════════════════════════════
queue_run() {
    local id="" url="" dest_field="" dest="" type_mode="" status="" summary="" err="" processed=0
    local rc=0
    if [[ -z "${PYBIN:-}" ]] && ! command -v python3 >/dev/null 2>&1; then
        msg_err "No hay un intérprete de Python disponible para la cola."
        return 1
    fi
    py_core queue recover >/dev/null 2>&1 || true
    while true; do
        id="$(py_core queue claim --pid "$$" 2>/dev/null)" || rc=$?
        if (( rc != 0 )); then
            msg_err "No se pudo consultar la cola de descargas (código $rc)."
            break
        fi
        # El identificador debe ser un entero positivo antes de usarlo.
        if ! [[ "$id" =~ ^[0-9]+$ ]] || (( 10#$id < 1 )); then
            msg_err "La cola devolvió un identificador no válido: '${id}'"
            break
        fi
        QUEUE_ACTIVE_TASK="$id"
        QUEUE_TASK_ID="$id"
        url="$(py_core queue get "$id" --field url)"
        dest_field="$(py_core queue get "$id" --field dest 2>/dev/null || true)"
        if [[ -n "$dest_field" ]]; then
            dest="$(resolve_destination "$dest_field")" || dest="$(destination_display)"
        else
            dest="$(destination_display)"
        fi
        printf '\n%s%s══ Cola: tarea #%s ══%s\n  %s\n' "$BOLD" "$CYAN" "$id" "$NC" "$url"
        detect_url_type "$url"
        type_mode="$DETECTED_TYPE"
        download_job "$url" "$type_mode" "$DETECTED_COUNT" "$dest" || true
        print_job_summary "$dest"
        case "$JOB_STATUS" in
            ok) status="done" ;;
            partial) status="partial" ;;
            cancelled) status="cancelled" ;;
            *) status="failed" ;;
        esac
        summary="{\"ok\":$JOB_OK,\"errors\":$JOB_ERRORS,\"verify_failed\":$JOB_VERIFY_FAILED,\"class\":\"$JOB_CLASS\"}"
        err=""
        [[ "$status" == "failed" || "$status" == "partial" ]] && err="$(status_label "$JOB_STATUS")"
        py_core queue finish "$id" "$status" --summary "$summary" --error "$err" >/dev/null 2>&1 || true
        QUEUE_ACTIVE_TASK=""
        QUEUE_TASK_ID=""
        processed=$((processed + 1))
    done
    echo -e "\n  ${GREEN}Cola procesada:${NC} $processed tarea(s) en esta ejecución."
    return 0
}

menu_queue() {
    py_core queue recover >/dev/null 2>&1 || true
    while true; do
        clear_screen
        echo -e "${BOLD}${CYAN}══ Cola de descargas ══${NC}\n"
        py_core queue list || true
        echo ""
        echo -e "  ${DIM}La cola se conserva al cerrar OtterRip. Ctrl+C durante el procesamiento pausa la tarea en curso.${NC}"
        echo ""
        echo -e "  ${CYAN}1)${NC} Añadir URLs"
        echo -e "  ${CYAN}2)${NC} Procesar la cola ahora"
        echo -e "  ${CYAN}3)${NC} Pausar (pendientes)      ${CYAN}4)${NC} Reanudar"
        echo -e "  ${CYAN}5)${NC} Cancelar tarea           ${CYAN}6)${NC} Reintentar fallidas/parciales"
        echo -e "  ${CYAN}7)${NC} Limpiar terminadas       ${CYAN}0)${NC} Volver"
        echo ""
        local o="" ids=""
        read -rp "  Opción: " o || return 0
        case "$o" in
            1)
                echo -e "  Pega una URL por línea. Línea vacía para terminar."
                local -a urls=()
                local line=""
                while read -rp "  URL: " line && [[ -n "$(trim_input "$line")" ]]; do
                    urls+=("$(trim_input "$(strip_wrapping_quotes "$line")")")
                done
                if ((${#urls[@]})); then
                    py_core queue add "${urls[@]}" --dest "$LAST_DOWNLOAD_FOLDER" || echo -e "  ${RED}Nada añadido (URLs inválidas o duplicadas).${NC}"
                fi
                sleep 1.2
                ;;
            2) queue_run; pause_enter ;;
            3|4|5|6)
                read -rp "  ID(s) separados por espacio, o 'all': " ids || return 0
                local verb
                case "$o" in 3) verb=pause ;; 4) verb=resume ;; 5) verb=cancel ;; *) verb=retry ;; esac
                # shellcheck disable=SC2086  # separación intencionada por palabras
                py_core queue "$verb" $ids || true
                sleep 1
                ;;
            7) echo "  Eliminadas: $(py_core queue clear)"; sleep 1 ;;
            0|"") return 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  BIBLIOTECA, REPARADOR, LETRAS AVANZADAS, CONVERSOR, DIAGNÓSTICO
# ═══════════════════════════════════════════════════════════════
menu_library() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Biblioteca musical ══${NC}\n"
    echo -e "  Detecta duplicados REALES (mismo audio, no solo mismo nombre), metadatos"
    echo -e "  incompletos, álbumes incompletos, archivos sin portada y sin letra."
    echo -e "  ${DIM}Solo lectura: no modifica ningún archivo.${NC}\n"
    ask_path "Carpeta a analizar" "$(destination_display)" || { sleep 1; return 0; }
    py_core library report "$ASKED_PATH" --save || true
    pause_enter
}

menu_repair() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Reparador de metadatos ══${NC}\n"
    echo -e "  Normaliza etiquetas (espacios, '- Topic', IDs de vídeo, 'Artista - Título')."
    echo -e "  Conserva portadas, letras y etiquetas existentes; nunca vacía campos."
    echo -e "  Antes de modificar se hace una copia en: ${DIM}$OTTERRIP_STATE_DIR/backups${NC}\n"
    ask_path "Archivo o carpeta" "$(destination_display)" || { sleep 1; return 0; }
    local target="$ASKED_PATH" noise=""
    local -a extra=()
    if confirm "¿Quitar también '(Official Video)', '[Lyrics]'... del título?"; then
        extra+=(--clean-noise)
        noise="sí"
    fi
    echo -e "\n  ${YELLOW}Simulación (no se modifica nada)${NC}${noise:+ — limpieza de ruido activada}\n"
    py_core repair "$target" "${extra[@]}" || true
    echo ""
    if confirm "¿Aplicar estos cambios?"; then
        py_core repair "$target" --apply "${extra[@]}" || true
    else
        echo -e "  ${DIM}Sin cambios.${NC}"
    fi
    pause_enter
}

menu_lyrics_manager() {
    while true; do
        clear_screen
        echo -e "${BOLD}${CYAN}══ Gestor avanzado de letras ══${NC}\n"
        echo -e "  ${CYAN}1)${NC} Buscar letras en una carpeta/archivo (respeta las existentes)"
        echo -e "  ${CYAN}2)${NC} Comparar resultados de proveedores para una pista"
        echo -e "  ${CYAN}3)${NC} Ver la letra incrustada de una pista"
        echo -e "  ${CYAN}4)${NC} Ajustar desfase temporal (.lrc o incrustada)"
        echo -e "  ${CYAN}5)${NC} Convertir LRC → TXT"
        echo -e "  ${CYAN}6)${NC} Corrección manual: incrustar un .lrc/.txt propio"
        echo -e "  ${CYAN}7)${NC} Verificar letras de la biblioteca (lyrics verify)"
        echo -e "  ${CYAN}8)${NC} Reparar letras de la biblioteca (lyrics repair)"
        echo -e "  ${CYAN}0)${NC} Volver"
        echo ""
        local o="" t="" ms="" src=""
        read -rp "  Opción: " o || return 0
        lyrics_common_args
        case "$o" in
            1)
                ask_path "Carpeta o archivo" "$(destination_display)" || { sleep 1; continue; }
                local ow=()
                confirm "¿Sustituir letras ya existentes (se hace copia de seguridad)?" && ow=(--overwrite)
                if [[ -f "$ASKED_PATH" ]]; then
                    # Archivo único: extraer extensión en minúsculas y procesar una sola vez.
                    local ext="${ASKED_PATH##*.}"
                    ext="${ext,,}"
                    case "$ext" in
                        mp3|flac|m4a)
                            py_core lyrics "$ASKED_PATH" "$ext" "${LYRICS_ARGS[@]}" --jobs "$PARALLEL_JOBS" "${ow[@]}" || true
                            ;;
                        *)
                            msg_err "Extensión no soportada para letras: .$ext (usa mp3, flac o m4a)"
                            ;;
                    esac
                elif [[ -d "$ASKED_PATH" ]]; then
                    # Directorio: solo lanzar py_core para formatos que realmente tengan archivos.
                    local f found_any=false
                    for f in mp3 flac m4a; do
                        if find "$ASKED_PATH" -maxdepth 3 -type f -iname "*.$f" -print -quit 2>/dev/null | grep -q .; then
                            py_core lyrics "$ASKED_PATH" "$f" "${LYRICS_ARGS[@]}" --jobs "$PARALLEL_JOBS" "${ow[@]}" || true
                            found_any=true
                        fi
                    done
                    if [[ "$found_any" == "false" ]]; then
                        msg_warn "No se encontraron archivos de audio (mp3/flac/m4a) en: $ASKED_PATH"
                    fi
                else
                    msg_err "Ruta no válida: $ASKED_PATH"
                fi
                pause_enter
                ;;
            2)
                ask_path "Archivo de audio" "" || { sleep 1; continue; }
                py_core lyrics-tool compare "$ASKED_PATH" "${LYRICS_ARGS[@]}" || true
                pause_enter
                ;;
            3)
                ask_path "Archivo de audio" "" || { sleep 1; continue; }
                py_core lyrics-tool show "$ASKED_PATH" || true
                pause_enter
                ;;
            4)
                ask_path "Archivo .lrc o de audio" "" || { sleep 1; continue; }
                t="$ASKED_PATH"
                read -rp "  Desfase en milisegundos (+ retrasa, - adelanta): " ms || continue
                if [[ "$ms" =~ ^[+-]?[0-9]+$ ]]; then
                    py_core lyrics-tool shift "$t" "$ms" || true
                else
                    echo -e "  ${RED}✘ Valor inválido.${NC}"
                fi
                pause_enter
                ;;
            5)
                ask_path "Archivo .lrc" "" || { sleep 1; continue; }
                py_core lyrics-tool convert "$ASKED_PATH" || true
                pause_enter
                ;;
            6)
                ask_path "Archivo de audio" "" || { sleep 1; continue; }
                t="$ASKED_PATH"
                ask_path "Archivo .lrc/.txt con la letra" "" || { sleep 1; continue; }
                src="$ASKED_PATH"
                py_core lyrics-tool set "$t" "$src" || true
                pause_enter
                ;;
            7)
                ask_path "Carpeta de biblioteca" "$(destination_display)" || { sleep 1; continue; }
                echo -e "\n  ${YELLOW}⚙${NC} Verificando letras en: $ASKED_PATH\n"
                local lang_arg=()
                [[ -n "$LYRICS_LANG" ]] && lang_arg=(--lang "$LYRICS_LANG")
                py_core lyrics-manage verify "$ASKED_PATH" "${lang_arg[@]}" || true
                pause_enter
                ;;
            8)
                ask_path "Carpeta de biblioteca" "$(destination_display)" || { sleep 1; continue; }
                local lang_arg=()
                [[ -n "$LYRICS_LANG" ]] && lang_arg=(--lang "$LYRICS_LANG")
                echo -e "\n  ${YELLOW}Simulación de reparación (no se modifica nada)${NC}\n"
                py_core lyrics-manage repair "$ASKED_PATH" --dry-run "${lang_arg[@]}" || true
                echo ""
                if confirm "¿Aplicar estos cambios?"; then
                    echo -e "\n  ${YELLOW}⚙${NC} Reparando letras...\n"
                    py_core lyrics-manage repair "$ASKED_PATH" "${lang_arg[@]}" || true
                else
                    echo -e "  ${DIM}Sin cambios.${NC}"
                fi
                pause_enter
                ;;
            0|"") return 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
}

menu_convert() {
    clear_screen
    echo -e "${BOLD}${CYAN}══ Conversor de audio (archivos locales) ══${NC}\n"
    echo -e "  Formatos de salida: mp3, flac, m4a, wav, ogg."
    echo -e "  Conserva etiquetas, portada y letra (excepto WAV). Nunca sobrescribe."
    echo -e "  ${YELLOW}Nota:${NC} convertir un archivo con pérdida (MP3/M4A/OGG) a FLAC/WAV NO mejora su calidad.\n"
    ask_path "Archivo o carpeta de origen" "" || { sleep 1; return 0; }
    local src="$ASKED_PATH" to="" q="" dest="" dflt=""
    read -rp "  Formato destino [mp3/flac/m4a/wav/ogg]: " to || return 0
    to="${to,,}"
    case "$to" in mp3|flac|m4a|wav|ogg) ;; *) echo -e "  ${RED}✘ Formato no soportado.${NC}"; sleep 1.2; return 0 ;; esac
    echo -e "  ${DIM}Calidad: low | medium | high | max (mp3/m4a: también 192k...; ogg: q5...; wav: 16 o 24)${NC}"
    read -rp "  Calidad (Enter=por defecto del formato): " q || return 0
    dflt="$DOWNLOADS/Converted"
    read -rp "  Carpeta de salida (Enter=$dflt): " dest || return 0
    [[ -z "$dest" ]] && dest="$dflt"
    dest="$(expand_tilde "$(strip_wrapping_quotes "$dest")")"
    local -a args=(convert "$src" --to "$to" --dest "$dest")
    [[ -n "$q" ]] && args+=(--quality "$q")
    py_core "${args[@]}" || true
    pause_enter
}

menu_diagnostics() {
    while true; do
        clear_screen
        echo -e "${BOLD}${CYAN}══ Diagnóstico y mantenimiento ══${NC}\n"
        echo -e "  ${CYAN}1)${NC} Ejecutar diagnóstico del sistema (doctor)"
        echo -e "  ${CYAN}2)${NC} Actualizar yt-dlp (entorno aislado)"
        echo -e "  ${CYAN}3)${NC} Reparar/Reinstalar dependencias"
        echo -e "  ${CYAN}4)${NC} Ver últimas líneas del registro"
        echo -e "  ${CYAN}5)${NC} Recuperar sesiones interrumpidas"
        echo -e "  ${CYAN}6)${NC} Guardar informe de diagnóstico en un archivo"
        echo -e "  ${CYAN}0)${NC} Volver"
        echo ""
        local o=""
        read -rp "  Opción: " o || return 0
        case "$o" in
            1) py_core doctor --dest "$DOWNLOADS" || true; pause_enter ;;
            2)
                if confirm "¿Actualizar yt-dlp en el venv de OtterRip?"; then
                    if [[ "$PYBIN" != "$VENV_DIR/bin/python" ]]; then
                        create_venv && PYBIN="$VENV_DIR/bin/python" \
                            && { libs_ok_for "$PYBIN" || venv_pip_install -r "$REQUIREMENTS_FILE"; }
                    fi
                    if [[ "$PYBIN" == "$VENV_DIR/bin/python" ]]; then
                        venv_pip_install "yt-dlp[default]" || true
                        YTDLP_BIN="$(find_ytdlp)"
                        export YTDLP_BIN
                        echo "  yt-dlp: $(ytdlp --version 2>/dev/null || echo 'desconocido')"
                    fi
                fi
                pause_enter
                ;;
            3) install_deps --check-deps || true; pause_enter ;;
            4) tail -n 30 -- "$LOG_FILE" 2>/dev/null || echo "  (registro vacío)"; pause_enter ;;
            5) recover_sessions; pause_enter ;;
            6)
                local out=""
                out="$OTTERRIP_STATE_DIR/diagnostico-$(date +%Y%m%d-%H%M%S).txt"
                {
                    echo "OtterRip $OTTERRIP_VERSION — $(date)"
                    py_core doctor --dest "$DOWNLOADS" || true
                    echo; echo "── Registro (últimas 80 líneas) ──"
                    tail -n 80 -- "$LOG_FILE" 2>/dev/null || true
                } > "$out" 2>&1
                echo "  Informe guardado en: $out"
                pause_enter
                ;;
            0|"") return 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  ACTUALIZACIONES Y PROCESO POR LOTES
# ═══════════════════════════════════════════════════════════════

# Compara dos versiones con puntos: 0 si $1 es mayor que $2.
# @brief Compara dos versiones separadas por puntos.
# @param $1 Primera versión.
# @param $2 Segunda versión.
# @return 0 si la primera es mayor; 1 si es menor o igual.
# @side-effects Ninguno.
version_gt() {
    local a="${1-}" b="${2-}" i=0 x="" y=""
    local -a A=() B=()
    IFS='.' read -r -a A <<< "$a" || true
    IFS='.' read -r -a B <<< "$b" || true
    for ((i = 0; i < ${#A[@]} || i < ${#B[@]}; i++)); do
        x="${A[i]:-0}"; y="${B[i]:-0}"
        x="${x//[!0-9]/}"; y="${y//[!0-9]/}"
        x="${x:-0}"; y="${y:-0}"
        (( 10#$x > 10#$y )) && return 0
        (( 10#$x < 10#$y )) && return 1
    done
    return 1
}

# Aviso (no bloqueante) si hay una versión más reciente publicada en GitHub.
# @brief Avisa (sin bloquear) si hay una versión más reciente en GitHub.
# @param $1 (no usado)
# @return 0 siempre.
# @side-effects Realiza dos peticiones HTTPS a GitHub (solo con terminal y red).
check_for_updates() {
    [[ "${OTTERRIP_CHECK_UPDATES:-true}" == "true" ]] || return 0
    [[ -t 1 ]] || return 0
    command -v curl >/dev/null 2>&1 || return 0
    local tags="" tag="" latest="" status=""
    tags="$(curl -s --max-time 8 --proto '=https' --proto-redir '=https' \
        "https://api.github.com/repos/$OTTERRIP_REPO/tags" 2>/dev/null || true)"
    [[ -n "$tags" ]] || return 0
    while IFS= read -r tag; do
        tag="${tag//[\" ]/}"
        tag="${tag#name:}"
        tag="${tag#v}"
        [[ "$tag" =~ ^[0-9]+(\.[0-9]+)*$ ]] || continue
        if [[ -z "$latest" ]] || version_gt "$tag" "$latest"; then
            latest="$tag"
        fi
    done < <(printf '%s' "$tags" | grep -o '"name": *"v[^"]*"' || true)
    [[ -n "$latest" ]] || return 0
    version_gt "$latest" "$OTTERRIP_VERSION" || return 0
    # Se confirma que la etiqueta existe (HEAD al tag).
    status="$(curl -sI --max-time 6 --proto '=https' --proto-redir '=https' \
        -o /dev/null -w '%{http_code}' \
        "https://github.com/$OTTERRIP_REPO/releases/tag/v$latest" 2>/dev/null || true)"
    [[ "$status" == "200" ]] || return 0
    msg_info "Hay una versión nueva disponible: v$latest (tu versión: v$OTTERRIP_VERSION)."
    return 0
}

# Procesa un archivo con una URL por línea ("#" = comentario).
# Formato admitido por línea: "URL [destino]".
# @brief Procesa un archivo de URLs de forma secuencial.
# @param $1 Ruta del archivo (una URL por línea; "#" comenta).
# @return 0 si se procesó el lote; 2 si el archivo no existe.
# @side-effects Descarga los elementos del lote y registra el historial.
run_batch() {
    local file="${1-}" line="" url="" dest="" target=""
    local total=0 ok=0 failed=0 skipped=0
    if [[ -z "$file" || ! -f "$file" ]]; then
        msg_err "Archivo de lote no encontrado: ${file}"
        return 2
    fi
    local -a entries=()
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        line="$(trim_input "$line")"
        [[ -z "$line" || "$line" == \#* ]] && continue
        entries+=("$line")
    done < "$file"
    if ((${#entries[@]} == 0)); then
        msg_info "El archivo no contiene ninguna URL."
        return 0
    fi
    printf '\n%s%s══ Lote: %s URL(s) ══%s\n' "$BOLD" "$CYAN" "${#entries[@]}" "$NC"
    local entry=""
    local -a parts=()
    for entry in "${entries[@]}"; do
        parts=()
        read -r -a parts <<< "$entry" || true
        url="${parts[0]:-}"
        dest=""
        if ((${#parts[@]} > 1)); then
            dest="${parts[*]:1}"
        fi
        total=$((total + 1))
        if ! is_safe_url "$url"; then
            msg_err "URL no válida en el lote: $url"
            failed=$((failed + 1))
            continue
        fi
        if [[ -n "$dest" ]]; then
            if ! target="$(resolve_destination "$dest")"; then
                msg_err "Destino no válido en el lote: $dest"
                failed=$((failed + 1))
                continue
            fi
        else
            target="$(destination_display)"
        fi
        detect_url_type "$url"
        download_job "$url" "$DETECTED_TYPE" "$DETECTED_COUNT" "$target" || true
        case "$JOB_STATUS" in
            ok) ok=$((ok + 1)) ;;
            cancelled) skipped=$((skipped + 1)) ;;
            *) failed=$((failed + 1)) ;;
        esac
    done
    printf '\n%s%s══ Resumen del lote ══%s\n' "$BOLD" "$CYAN" "$NC"
    printf '  %sURLs procesadas:%s %s\n' "$BOLD" "$NC" "$total"
    printf '  %sCon éxito:%s       %s\n' "$BOLD" "$NC" "$ok"
    printf '  %sCon errores:%s     %s\n' "$BOLD" "$NC" "$failed"
    printf '  %sCanceladas:%s      %s\n' "$BOLD" "$NC" "$skipped"
    return 0
}

# ═══════════════════════════════════════════════════════════════
#  INICIO
# ═══════════════════════════════════════════════════════════════
usage() {
    cat <<EOF
OtterRip $OTTERRIP_VERSION — descargador de audio con letras

Uso: $(basename "$0") [opción | URL [carpeta_destino]]
  (sin opción)           menú interactivo
  <URL> [carpeta_destino] descarga directa (no interactiva) y salida con código de retorno
  [-q|--quality <n>] [-j|--jobs <n>] [--no-lyrics] [--cookies-from-browser <nav>] [--cookies <archivo>] [--proxy <url>] <URL> [carpeta]
                                            descarga directa con flags rápidos (sobreescribe config solo esta ejecución)
  --add|--enqueue <URL...> añade una o más URLs a la cola sin abrir el menú
  --batch <archivo>       procesa una lista de URLs (una por línea, "#" = comentario)
  --proxy <url>           usa un proxy (http/https/socks4/socks5) en esta ejecución
  --dry-run               muestra el plan de descarga sin descargar nada
  --verbose               muestra los comandos completos de yt-dlp
  --debug                 traza de depuración (set -x) y no borra temporales
  --history               muestra el historial de descargas
  --doctor               diagnóstico del sistema (puede descargar el backend)
  --queue-run            procesa la cola de descargas sin menús
  --check-deps           fuerza verificación completa de dependencias (ignora caché)
  --version              muestra la versión
  --help                 esta ayuda

Variables: OTTERRIP_DOWNLOADS, PARALLEL_JOBS, OTTERRIP_ASSUME_YES=1, OTTERRIP_YTDLP,
  OTTERRIP_COOKIES_BROWSER, OTTERRIP_COOKIES_FILE, OTTERRIP_PROXY, OTTERRIP_NOTIFY,
  OTTERRIP_CHECK_UPDATES
EOF
}

# Para --doctor y pruebas: elige intérprete sin instalar nada.
select_python_readonly() {
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        PYBIN="$VENV_DIR/bin/python"
    else
        PYBIN="$(command -v python3 2>/dev/null || true)"
    fi
}

main() {
    # La plantilla de sesión nunca debe venir corrupta de una config externa.
    validate_session_template "$SESSION_PRINT_TEMPLATE" ||
        SESSION_PRINT_TEMPLATE="$SESSION_PRINT_TEMPLATE_DEFAULT"

    # Flags globales: se extraen de la lista y pueden ir en cualquier posición.
    local -a gflags=()
    local garg=""
    for garg in "$@"; do
        case "$garg" in
            --dry-run) DRY_RUN=true ;;
            --verbose) VERBOSITY=1 ;;
            --debug) VERBOSITY=2 ;;
            *) gflags+=("$garg") ;;
        esac
    done
    if ((${#gflags[@]})); then
        set -- "${gflags[@]}"
    else
        set --
    fi
    (( VERBOSITY >= 2 )) && set -x

    # Bloque 1: opciones sin efectos secundarios (salen inmediatamente).
    case "${1-}" in
        --version|-V) printf 'OtterRip %s\n' "$OTTERRIP_VERSION"; return 0 ;;
        --help|-h) usage; return 0 ;;
        --doctor)
            select_python_readonly
            [[ -n "$PYBIN" ]] || { echo "python3 no encontrado."; return 13; }
            python_version_ok || { echo "Python 3.9+ requerido para --doctor." >&2; return 1; }
            ensure_backend || return 1
            py_core doctor --dest "$DOWNLOADS"
            return $?
            ;;
    esac

    # Bloque 2: permitir pasar los argumentos válidos; rechazar el resto.
    case "${1-}" in
        "") ;;
        --queue-run) ;;
        --check-deps) ;;
        --add|--enqueue) ;;
        --history) ;;
        --batch|--proxy) ;;
        -q|--quality|-j|--jobs|--no-lyrics|--cookies-from-browser|--cookies) ;;
        *)
            if is_http_url "${1-}"; then
                :
            else
                msg_err "Opción desconocida: $1"
                usage >&2
                return 2
            fi
            ;;
    esac

    # Inicializar directorios y config ANTES de cualquier operación con efectos.
    init_runtime_dirs
    load_config || true

    # --history: muestra el historial de descargas y termina (no requiere deps).
    if [[ "${1-}" == "--history" ]]; then
        show_history 50
        return 0
    fi

    # --check-deps ahora tiene los directorios creados para write_deps_cache.
    if [[ "${1-}" == "--check-deps" ]]; then
        install_deps --check-deps
        return $?
    fi

    if ! install_deps; then
        echo -e "\n${RED}No se pudo iniciar OtterRip: faltan dependencias.${NC}\n"
        return 1
    fi
    refresh_syncedlyrics_state || true
    recover_sessions || true

    # Aviso de versión nueva (no bloqueante, solo con terminal interactivo).
    check_for_updates || true

    # --add / --enqueue: añadir URLs a la cola sin menú interactivo.
    if [[ "${1-}" == "--add" || "${1-}" == "--enqueue" ]]; then
        shift || true
        if (( $# == 0 )); then
            echo "Espera al menos una URL." >&2
            return 2
        fi
        py_core queue add "$@" --dest "$LAST_DOWNLOAD_FOLDER" || return 1
        return 0
    fi

    # Descarga directa no interactiva con flags rápidos opcionales:
    #   otterrip [-q|--quality <n>] [-j|--jobs <n>] [--no-lyrics] [--cookies-from-browser <nav>]
    #           [--cookies <archivo>] [--proxy <url>] <URL> [carpeta]
    if is_http_url "${1-}" || [[ "${1-}" == "-q" || "${1-}" == "--quality" || "${1-}" == "-j" || "${1-}" == "--jobs" || "${1-}" == "--no-lyrics" || "${1-}" == "--cookies-from-browser" || "${1-}" == "--cookies" || "${1-}" == "--proxy" ]]; then
        while (( $# > 0 )); do
            case "${1-}" in
                -q|--quality)
                    shift || true
                    [[ -n "${1-}" ]] || { msg_err "Falta valor para --quality"; return 2; }
                    apply_quality_choice "${1-}" || { msg_err "Calidad inválida: ${1-} (usa 1-5)"; return 2; }
                    ;;
                -j|--jobs)
                    shift || true
                    if ! is_positive_int "${1-}" || (( 10#${1-} < 1 || 10#${1-} > MAX_WORKERS )); then
                        msg_err "Número de workers inválido: ${1-} (usa 1-32)"
                        return 2
                    fi
                    PARALLEL_JOBS="$(normalize_jobs "${1-}")"
                    ;;
                --no-lyrics)
                    FETCH_LYRICS="false"
                    ;;
                --proxy)
                    shift || true
                    local pvalue=""
                    if ! pvalue="$(normalize_proxy "${1-}")"; then
                        msg_err "Proxy no válido: ${1-} (usa http://, https://, socks5://...)"
                        return 2
                    fi
                    OTTERRIP_PROXY="$pvalue"
                    ;;
                --cookies-from-browser)
                    shift || true
                    if [[ -z "${1-}" || ! "${1-}" =~ ^[a-zA-Z0-9_.:+-]+$ ]]; then
                        msg_err "Valor inválido para --cookies-from-browser: ${1-}"
                        return 2
                    fi
                    COOKIES_BROWSER="${1-}"
                    COOKIES_FILE=""
                    ;;
                --cookies)
                    shift || true
                    local cpath=""
                    cpath="$(expand_tilde "$(strip_wrapping_quotes "${1-}")")"
                    if [[ -z "$cpath" || ! -f "$cpath" || ! -r "$cpath" ]]; then
                        msg_err "Archivo de cookies no encontrado o no legible: ${1-}"
                        return 2
                    fi
                    COOKIES_FILE="$cpath"
                    COOKIES_BROWSER=""
                    ;;
                *)
                    break
                    ;;
            esac
            shift || true
        done
        if ! is_http_url "${1-}"; then
            msg_err "Se esperaba una URL válida tras las opciones rápidas."
            usage >&2
            return 2
        fi
        local dl_dest=""
        if [[ -n "${2-}" ]]; then
            dl_dest="$(resolve_destination "$2")" || { msg_err "Ruta de destino inválida: $2"; return 2; }
        else
            dl_dest="$(destination_display)"
        fi
        detect_url_type "$1"
        download_job "$1" "$DETECTED_TYPE" "$DETECTED_COUNT" "$dl_dest" || return $?
        print_job_summary "$dl_dest"
        [[ "$JOB_STATUS" == "ok" || "$JOB_STATUS" == "partial" ]] || return 1
        return 0
    fi

    # --batch <archivo>: procesa una lista de URLs.
    if [[ "${1-}" == "--batch" ]]; then
        shift || true
        [[ -n "${1-}" ]] || { msg_err "Falta el archivo para --batch"; return 2; }
        run_batch "$1"
        return $?
    fi

    if [[ "${1-}" == "--queue-run" ]]; then
        queue_run
        return 0
    fi
    menu
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

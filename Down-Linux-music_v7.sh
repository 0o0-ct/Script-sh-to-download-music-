#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  OtterRip v7.2 — Descargador de audio + letras
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

OTTERRIP_VERSION="7.2.0"

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
BACKEND_SHA256="b488fa97cbe8053c68fad9b1110a5e5aa5669e5d88497f3db80b63e6087036ef"
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
export OTTERRIP_CONFIG_DIR OTTERRIP_DATA_DIR OTTERRIP_STATE_DIR OTTERRIP_CACHE_DIR
mkdir -p -- "$OTTERRIP_CONFIG_DIR" "$OTTERRIP_DATA_DIR" "$OTTERRIP_STATE_DIR/sessions" \
    "$OTTERRIP_CACHE_DIR" 2>/dev/null || true
chmod 700 -- "$OTTERRIP_CONFIG_DIR" "$OTTERRIP_DATA_DIR" "$OTTERRIP_STATE_DIR" 2>/dev/null || true

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
mkdir -p -- "$DOWNLOADS"

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

backend_archive_paths_are_safe() {
    local archive="${1-}"
    python3 - "$archive" <<'PYEOF'
import sys
import tarfile
from pathlib import PurePosixPath

archive_path = sys.argv[1]
try:
    with tarfile.open(archive_path, "r:gz") as archive:
        seen = set()
        for member in archive.getmembers():
            name = member.name.removeprefix("./")
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
            seen.add(name)
except (OSError, tarfile.TarError, ValueError):
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

recover_backend_install() {
    local tmp_dir="" backup="" owner="" basename=""
    [[ -d "$APP_ROOT" ]] || return 0
    for tmp_dir in "$APP_ROOT"/".v${OTTERRIP_VERSION}".[0-9]*.*; do
        [[ -d "$tmp_dir" ]] || continue
        basename="${tmp_dir##*/}"
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
                    return 1
                fi
            fi
        fi
        rm -rf -- "$tmp_dir"
    done
    return 0
}

BACKEND_LOCK_DIR=""

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

# Plantilla de sesión: única fuente de verdad en Python (session.PRINT_TEMPLATE).
# Se duplica aquí para no depender de Python al sourcear el script; un test
# (tests/bats) comprueba que ambas coinciden.
SESSION_PRINT_TEMPLATE='after_move:{"filepath":%(filepath|null)j,"title":%(title|null)j,"artist":%(artist|null)j,"uploader":%(uploader|null)j,"creator":%(creator|null)j,"album":%(album|null)j,"playlist_title":%(playlist_title|null)j,"release_year":%(release_year|null)j,"upload_date":%(upload_date|null)j,"genre":%(genre|null)j,"track_number":%(track_number|null)j,"disc_number":%(disc_number|null)j,"playlist_index":%(playlist_index|null)j,"id":%(id|null)j,"extractor_key":%(extractor_key|null)j,"duration":%(duration|null)j}'

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
        if [[ "$size" =~ ^[0-9]+$ ]] && (( size > 1048576 )); then
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
        if [[ "$WORK_DONE" == "true" ]] || ! session_dir_has_entries "$WORK_DIR"; then
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

normalize_jobs() {
    local v="${1:-3}"
    is_positive_int "$v" || v=3
    while [[ ${#v} -gt 1 && "${v:0:1}" == "0" ]]; do
        v="${v:1}"
    done
    if [[ ${#v} -gt 2 ]]; then
        v=32
    else
        v=$((10#$v))
        (( v > 32 )) && v=32
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
        s="$(python3 -c 'import sys; b=sys.argv[1].encode("utf-8")[:200]; sys.stdout.write(b.decode("utf-8","ignore"))' "$s" 2>/dev/null || printf '%s' "${s:0:80}")"
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
        resolved="$(realpath -m -- "$input" 2>/dev/null || printf '%s' "$input")"
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
    resolved="$(realpath -m -- "$DOWNLOADS/$rel" 2>/dev/null || printf '%s' "$DOWNLOADS/$rel")"
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
        echo -e "  ${RED}✘ No existe: $raw${NC}"
        return 1
    fi
    ASKED_PATH="$(realpath -- "$raw" 2>/dev/null || printf '%s' "$raw")"
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

load_config() {
    [[ -f "$CONFIG_FILE" ]] || return 0
    local key="" value="" norm=""
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        [[ "$key" =~ ^[A-Z_]+$ ]] || continue
        value="${value%$'\r'}"
        case "$key" in
            QUALITY_CHOICE) apply_quality_choice "$value" || true ;;
            DUR_MIN|DUR_MAX)
                if is_positive_int "$value"; then printf -v "$key" '%s' "$((10#$value))"; fi ;;
            PARALLEL_JOBS)
                if [[ "$ENV_PARALLEL_SET" != "true" ]]; then PARALLEL_JOBS="$(normalize_jobs "$value")"; fi ;;
            ALBUM_LAYOUT)
                if [[ "$value" == "single" || "$value" == "split" ]]; then ALBUM_LAYOUT="$value"; fi ;;
            FETCH_LYRICS|LYRICS_PREFER_SYNCED|LYRICS_UPGRADE_PLAIN)
                if is_bool "$value"; then printf -v "$key" '%s' "$value"; fi ;;
            LYRICS_MODE)
                if [[ "$value" == "embed" || "$value" == "file" ]]; then LYRICS_MODE="$value"; fi ;;
            LRCLIB_HOST)
                if norm="$(normalize_host "$value")"; then LRCLIB_HOST="$norm"; fi ;;
            LYRICS_LANG)
                if [[ "$value" =~ ^([a-z]{2})?$ ]]; then LYRICS_LANG="$value"; fi ;;
            LYRICS_PROVIDERS)
                case "$value" in lrclib|multiweb|lrclib,multiweb) LYRICS_PROVIDERS="$value" ;; esac ;;
            LAST_DOWNLOAD_FOLDER)
                if [[ -n "$value" && "$value" != *$'\t'* ]]; then LAST_DOWNLOAD_FOLDER="$value"; fi ;;
            COVER_LOCAL)
                if [[ -z "$value" || -f "$value" ]]; then COVER_LOCAL="$value"; fi ;;
        esac
    done < "$CONFIG_FILE"
    return 0
}

save_config() {
    local tmp="" key=""
    tmp="$(mktemp "$OTTERRIP_CONFIG_DIR/.config.XXXXXX" 2>/dev/null)" || return 0
    {
        echo "# OtterRip — generado automáticamente. Puedes editarlo; los valores inválidos se ignoran."
        for key in QUALITY_CHOICE DUR_MIN DUR_MAX PARALLEL_JOBS ALBUM_LAYOUT FETCH_LYRICS LYRICS_MODE \
                   LRCLIB_HOST LYRICS_LANG LYRICS_PROVIDERS LYRICS_PREFER_SYNCED LYRICS_UPGRADE_PLAIN \
                   LAST_DOWNLOAD_FOLDER COVER_LOCAL; do
            printf '%s=%s\n' "$key" "${!key}"
        done
    } > "$tmp" 2>/dev/null || { rm -f -- "$tmp"; return 0; }
    chmod 600 "$tmp" 2>/dev/null || true
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
    logfile="$(mktemp "${TMPDIR:-/tmp}/otterrip-pip.XXXXXX" 2>/dev/null || true)"
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
        return 0
    else
        create_venv || return 1
        PYBIN="$vpy"
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

ytdlp() {
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

install_deps() {
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

    echo -e "\n${GREEN}${BOLD}  Dependencias listas.${NC}\n"
    log_event INFO "dependencias OK (python=$PYBIN yt-dlp=$YTDLP_BIN)"
    sleep 0.6
}

# ═══════════════════════════════════════════════════════════════
#  DETECCIÓN DE URL (SIN EVAL)
# ═══════════════════════════════════════════════════════════════
DETECTED_TYPE="song"
DETECTED_TITLE=""
DETECTED_COUNT="1"
DETECTION_FALLBACK=false

detect_url_type() {
    local url="${1-}" lower="${1,,}" no_playlist=0
    local -a lines=()
    DETECTED_TYPE="song"
    DETECTED_TITLE=""
    DETECTED_COUNT="1"
    DETECTION_FALLBACK=false

    # Un enlace /watch?...&list=... se trata como canción (igual que v6.1).
    if [[ "$lower" =~ ^https?://(www\.)?(youtube\.com|youtu\.be)/watch\? ]] && [[ "$lower" == *"list="* ]]; then
        no_playlist=1
    fi

    mapfile -t lines < <(
        DETECT_URL="$url" DETECT_NO_PLAYLIST="$no_playlist" YTDLP_BIN="$YTDLP_BIN" \
        "${PYBIN:-python3}" - <<'PYEOF'
import json, os, re, subprocess, sys

url = os.environ.get("DETECT_URL", "")
no_playlist = os.environ.get("DETECT_NO_PLAYLIST", "0") == "1"
ytdlp_bin = os.environ.get("YTDLP_BIN", "").strip()

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
        "--print", template,
    ]
    if flat:
        args.insert(len(base), "--flat-playlist")
    if no_playlist:
        args.append("--no-playlist")
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
    )

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

count_playlist_entries() {
    local url="${1-}" n=0
    [[ -n "$url" ]] || { printf '0'; return; }
    n="$( { ytdlp --flat-playlist --skip-download --no-warnings --ignore-errors \
            --print "%(id)s" "$url" 2>/dev/null || true; } \
          | awk 'NF { c++ } END { print c+0 }')"
    n="${n//[[:space:]]/}"
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
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

# Bloqueo por carpeta de destino: evita dos instancias escribiendo el mismo
# archivo de historial. Si flock no existe se omite (se avisa en el log).
acquire_dest_lock() {
    local dest="${1-}"
    if ! command -v flock >/dev/null 2>&1; then
        log_event WARN "flock no disponible; sin bloqueo de destino"
        return 0
    fi
    { exec {DEST_LOCK_FD}>"$dest/.otterrip.lock"; } 2>/dev/null || { DEST_LOCK_FD=""; return 0; }
    if ! flock -n "$DEST_LOCK_FD"; then
        echo -e "  ${YELLOW}⏳${NC} Otra instancia de OtterRip usa este destino; esperando (máx. 120 s)..."
        if ! flock -w 120 "$DEST_LOCK_FD"; then
            echo -e "  ${RED}✘${NC} No se pudo obtener el bloqueo del destino."
            { exec {DEST_LOCK_FD}>&-; } 2>/dev/null || true
            DEST_LOCK_FD=""
            return 1
        fi
    fi
    return 0
}

release_dest_lock() {
    if [[ -n "${DEST_LOCK_FD:-}" ]]; then
        { exec {DEST_LOCK_FD}>&-; } 2>/dev/null || true
        DEST_LOCK_FD=""
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
        echo "  ║                 🦦 OTTER RIP v7.2                ║"
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
        printf '  %sDestino:%s     %s\n' "$BOLD" "$NC" "$dest"
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
            0) save_config; echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
        save_config
    done
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
    if [[ ${#w} -gt 2 ]] || (( 10#$w > 32 )); then
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
    path="$(realpath -m -- "$raw" 2>/dev/null || printf '%s' "$raw")"
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
                            venv_pip_install -r "$SCRIPT_DIR/requirements.txt" || true
                        fi
                    else
                        venv_pip_install -r "$SCRIPT_DIR/requirements.txt" || true
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
    (( total > 0 )) && filled=$(( done * width / total ))
    (( filled > width )) && filled=$width
    (( filled < 0 )) && filled=0
    for ((i = 0; i < width; i++)); do
        if (( i < filled )); then bar+="█"; else bar+="░"; fi
    done
    [[ -t 1 ]] || return 0
    printf '\r\033[K  [%s] %d/%d | OK:%d ERR:%d SKIP:%d | %d worker(s)' \
        "$bar" "$done" "$total" "$ok" "$err" "$skip" "$jobs"
}

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
        "$url"
    )
    "${cmd[@]}" >"$log" 2>&1
}

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
        "$url"
    )
    # En segundo plano + `wait`: así SIGTERM/SIGHUP al script interrumpen la espera
    # y el trap de limpieza mata a yt-dlp (en primer plano bash retrasaría el trap
    # hasta que yt-dlp terminara). Con pipefail, el estado es el de yt-dlp, no el de tee.
    ( "${cmd[@]}" 2>&1 | tee "$log" ) &
    local pid=$!
    CHILD_PIDS+=("$pid")
    wait "$pid" || rc=$?
    CHILD_PIDS=()
    return "$rc"
}

# Variante para la cola: segundo plano + sondeo del marcador de cancelación.
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
    while true; do
        ok=0
        for sf in "${worker_sessions[@]}"; do
            ok=$((ok + $(count_lines "$sf")))
        done
        errors="$(count_log_pattern '^ERROR:' "${worker_logs[@]}")"
        skipped="$(count_log_pattern "$SKIP_PATTERN" "${worker_logs[@]}")"
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
        sleep 1
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
    py_core verify-session --session "$session_final" --ext "$AFMT" --dest "$dest" \
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
    fix_metadata "$dest" "$AFMT" "$verified" "$COVER_LOCAL" || true

    if [[ "$FETCH_LYRICS" == "true" ]]; then
        echo -e "  ${YELLOW}⚙${NC} Buscando e incrustando letras (${JOB_OK} en cola)..."
        process_lyrics "$dest" "$AFMT" "$verified" "$lyr_summary" || true
        JOB_LYR_FOUND="$(summary_value "$lyr_summary" found 0)"
        JOB_LYR_NOTFOUND="$(summary_value "$lyr_summary" notfound 0)"
        JOB_LYR_ALREADY="$(summary_value "$lyr_summary" already 0)"
    fi
    return 0
}

# Ejecuta una descarga completa (sin menús). Rellena JOB_*; devuelve 0 si
# el trabajo terminó (aunque sea parcial) y 1 si no pudo ni empezar.
download_job() {
    local rc=0
    reset_job_state
    _download_job_body "$@" || rc=$?
    release_dest_lock
    return "$rc"
}

_download_job_body() {
    local url="${1-}" type_mode="${2-song}" detected_count="${3-}" dest="${4-}"
    local archive="$dest/.otterrip_archive" outtpl="" plflag=""

    if ! mkdir -p -- "$dest"; then
        echo -e "  ${RED}✘ No se pudo crear la carpeta de destino: $dest${NC}"
        JOB_STATUS="failed"; JOB_CLASS="permanent"
        return 1
    fi
    if [[ ! -w "$dest" ]]; then
        echo -e "  ${RED}✘ Sin permiso de escritura en: $dest${NC}"
        JOB_STATUS="failed"; JOB_CLASS="permanent"
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
    rm -rf -- "$WORK_DIR"
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
            if [[ -n "$(find "$d" -maxdepth 0 -mtime +14 2>/dev/null)" ]]; then
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
    py_core queue recover >/dev/null 2>&1 || true
    while true; do
        id="$(py_core queue claim --pid "$$" 2>/dev/null)" || break
        [[ "$id" =~ ^[0-9]+$ ]] || break
        QUEUE_ACTIVE_TASK="$id"
        QUEUE_TASK_ID="$id"
        url="$(py_core queue get "$id" --field url)"
        dest_field="$(py_core queue get "$id" --field dest 2>/dev/null || true)"
        if [[ -n "$dest_field" ]]; then
            dest="$(resolve_destination "$dest_field")" || dest="$(destination_display)"
        else
            dest="$(destination_display)"
        fi
        echo -e "\n${BOLD}${CYAN}══ Cola: tarea #$id ══${NC}\n  $url"
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
                local f
                for f in mp3 flac m4a; do
                    py_core lyrics "$ASKED_PATH" "$f" "${LYRICS_ARGS[@]}" --jobs "$PARALLEL_JOBS" "${ow[@]}" || true
                done
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
                            && { libs_ok_for "$PYBIN" || venv_pip_install -r "$SCRIPT_DIR/requirements.txt"; }
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
            3) install_deps || true; pause_enter ;;
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
#  INICIO
# ═══════════════════════════════════════════════════════════════
usage() {
    cat <<EOF
OtterRip $OTTERRIP_VERSION — descargador de audio con letras

Uso: $(basename "$0") [opción]
  (sin opción)   menú interactivo
  --doctor       diagnóstico del sistema (puede descargar el backend)
  --queue-run    procesa la cola de descargas sin menús
  --version      muestra la versión
  --help         esta ayuda

Variables: OTTERRIP_DOWNLOADS, PARALLEL_JOBS, OTTERRIP_ASSUME_YES=1, OTTERRIP_YTDLP
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
    case "${1-}" in
        --version|-V) printf 'OtterRip %s\n' "$OTTERRIP_VERSION"; return 0 ;;
        --help|-h) usage; return 0 ;;
        --doctor)
            select_python_readonly
            [[ -n "$PYBIN" ]] || { echo "python3 no encontrado."; return 13; }
            ensure_backend || return 1
            py_core doctor --dest "$DOWNLOADS"
            return $?
            ;;
        "") ;;
        --queue-run) ;;
        *) echo "Opción desconocida: $1" >&2; usage >&2; return 2 ;;
    esac

    load_config || true
    if ! install_deps; then
        echo -e "\n${RED}No se pudo iniciar OtterRip: faltan dependencias.${NC}\n"
        return 1
    fi
    refresh_syncedlyrics_state || true
    recover_sessions || true

    if [[ "${1-}" == "--queue-run" ]]; then
        queue_run
        return 0
    fi
    menu
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi

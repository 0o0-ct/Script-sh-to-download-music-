#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  OtterRip v5 — Descargador de audio · Arch Linux
#  Detección automática · Cookies opcionales · Pausas adaptativas
#  Clasificación de errores · Registro de fallidos · Reintentar fallidos
# ═══════════════════════════════════════════════════════════════

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS="$SCRIPT_DIR/downloads"
CONFIG_FILE="$DOWNLOADS/.otterrip.conf"
FAILED_FILE="$DOWNLOADS/failed_downloads.tsv"
LOG_DIR="$DOWNLOADS/logs"
mkdir -p "$DOWNLOADS" "$LOG_DIR"

# ── Colores ─────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; BOLD='\033[1m'; NC='\033[0m'

# ── Estado global ────────────────────────────────────────────────
AFMT="mp3"
AQ="0"
PP_VALUE=""
PROFILE="MP3 320 kbps"
COVER_LOCAL=""
DUR_MIN=60
DUR_MAX=480
ALBUM_LAYOUT="single"       # single | split
SONG_LAYOUT="artist"        # flat | artist
COOKIE_BROWSER="none"       # none | firefox | chrome | chromium | brave | edge | vivaldi | opera
SLEEP_MIN=2
SLEEP_MAX=5
MAX_RETRIES=5

RUN_SUCCESS=0
RUN_SKIPPED=0
RUN_FAILED=0
RUN_FILTERED=0
RUN_BOT=0
RUN_UNAVAILABLE=0
RUN_OTHER=0
LAST_RUN_LOG=""

# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════
_pacman() { sudo pacman -S --needed --noconfirm "$1" 2>/dev/null; }
_aur() {
    for h in yay paru; do
        if command -v "$h" &>/dev/null && "$h" -S --needed --noconfirm "$1" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}
_pip() { python3 -m pip install --break-system-packages -q "$1" 2>/dev/null; }

is_positive_int() { [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 > 0 )); }

safe_name() {
    local s="$1"
    s="${s//\\/_}"
    s="${s//\//_}"
    s="${s//$'\n'/ }"
    s="${s//$'\r'/ }"
    s="${s//\t/ }"
    s="$(printf '%s' "$s" | sed -E 's/[<>:"|?*]/_/g; s/[[:space:]]+/ /g; s/^ +| +$//g')"
    [[ -z "$s" ]] && s="Sin nombre"
    printf '%s' "$s"
}

cfg_save() {
    cat > "$CONFIG_FILE" <<EOF
AFMT='$AFMT'
AQ='$AQ'
PP_VALUE='$PP_VALUE'
PROFILE='$PROFILE'
COVER_LOCAL='$COVER_LOCAL'
DUR_MIN='$DUR_MIN'
DUR_MAX='$DUR_MAX'
ALBUM_LAYOUT='$ALBUM_LAYOUT'
SONG_LAYOUT='$SONG_LAYOUT'
COOKIE_BROWSER='$COOKIE_BROWSER'
SLEEP_MIN='$SLEEP_MIN'
SLEEP_MAX='$SLEEP_MAX'
MAX_RETRIES='$MAX_RETRIES'
EOF
    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
}

cfg_load() {
    [[ -f "$CONFIG_FILE" ]] || return 0
    # Config is generated only by this script; shell syntax is intentional.
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
}

pause_adaptive() {
    local min="$SLEEP_MIN" max="$SLEEP_MAX"
    (( min < 0 )) && min=0
    (( max < min )) && max="$min"
    if (( max == 0 )); then
        return 0
    fi
    local span=$((max - min + 1))
    local wait=$((RANDOM % span + min))
    echo -e "  ${CYAN}⏱${NC} Pausa adaptativa: ${wait}s"
    sleep "$wait"
}

pause_after_bot() {
    local seconds=$((15 + RANDOM % 16))
    echo -e "  ${YELLOW}🛡${NC} Señal anti-bot detectada. Pausa de ${seconds}s antes de continuar..."
    sleep "$seconds"
}

# ═══════════════════════════════════════════════════════════════
#  DEPENDENCIAS
# ═══════════════════════════════════════════════════════════════
install_deps() {
    echo -e "\n${BOLD}${CYAN}══ Verificando dependencias ══${NC}\n"

    if ! command -v ffmpeg &>/dev/null; then
        echo -e "  ${YELLOW}⚙${NC} Instalando ffmpeg..."
        _pacman ffmpeg || _aur ffmpeg || { echo -e "  ${RED}✘ No se pudo instalar ffmpeg${NC}"; exit 1; }
    else
        echo -e "  ${GREEN}✔${NC} ffmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
    fi

    if ! command -v yt-dlp &>/dev/null; then
        echo -e "  ${YELLOW}⚙${NC} Instalando yt-dlp..."
        _pacman yt-dlp || _aur yt-dlp || _pip yt-dlp \
            || { echo -e "  ${RED}✘ No se pudo instalar yt-dlp${NC}"; exit 1; }
    else
        echo -e "  ${GREEN}✔${NC} yt-dlp $(yt-dlp --version 2>/dev/null)"
        echo -e "  ${YELLOW}⟳${NC} Actualizando yt-dlp..."
        yt-dlp -U --quiet 2>/dev/null || true
    fi

    if ! python3 -c "import mutagen" 2>/dev/null; then
        echo -e "  ${YELLOW}⚙${NC} Instalando python-mutagen..."
        _pacman python-mutagen || _pip mutagen \
            || echo -e "  ${YELLOW}⚠${NC} python-mutagen no disponible (se omite reparación extra)"
    else
        echo -e "  ${GREEN}✔${NC} python-mutagen ya instalado"
    fi

    if ! command -v AtomicParsley &>/dev/null; then
        _pacman atomicparsley \
            && echo -e "  ${GREEN}✔${NC} AtomicParsley instalado" \
            || echo -e "  ${YELLOW}⚠${NC} AtomicParsley no disponible (portadas M4A limitadas)"
    else
        echo -e "  ${GREEN}✔${NC} AtomicParsley ya instalado"
    fi

    echo -e "\n${GREEN}${BOLD}  Dependencias listas.${NC}\n"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  METADATOS
# ═══════════════════════════════════════════════════════════════
fix_metadata() {
    local dir="$1" fmt="$2"
    python3 -c "import mutagen" 2>/dev/null || return 0

    python3 - "$dir" "$fmt" <<'PYEOF'
import sys, re
from pathlib import Path
try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, ID3NoHeaderError
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
except ImportError:
    sys.exit(0)

target = Path(sys.argv[1])
fmt = sys.argv[2]
pat = re.compile(r'^(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+)$')
ext = {"mp3": ".mp3", "flac": ".flac", "m4a": ".m4a"}.get(fmt, f".{fmt}")
count = 0

for fp in sorted(target.rglob(f"*{ext}")):
    try:
        if fmt == "mp3":
            MP3(fp)
            try:
                tags = ID3(fp)
            except ID3NoHeaderError:
                tags = ID3()
            artist_tag = tags.get("TPE1")
            title_tag = tags.get("TIT2")
            if (not artist_tag or not str(artist_tag)) and title_tag:
                m = pat.match(str(title_tag))
                if m:
                    tags.setall("TPE1", [TPE1(encoding=3, text=m.group("artist"))])
                    tags.setall("TIT2", [TIT2(encoding=3, text=m.group("title"))])
            tags.save(str(fp), v2_version=3, v1=2)
        elif fmt == "flac":
            audio = FLAC(fp)
            artist = audio.get("artist", [""])[0]
            title = audio.get("title", [""])[0]
            if not artist and title:
                m = pat.match(title)
                if m:
                    audio["artist"] = [m.group("artist")]
                    audio["title"] = [m.group("title")]
            audio.save()
        elif fmt == "m4a":
            audio = MP4(fp)
            if audio.tags:
                artist = audio.tags.get("\xa9ART", [""])[0]
                title = audio.tags.get("\xa9nam", [""])[0]
                if not artist and title:
                    m = pat.match(title)
                    if m:
                        audio.tags["\xa9ART"] = [m.group("artist")]
                        audio.tags["\xa9nam"] = [m.group("title")]
                audio.save()
        count += 1
    except Exception:
        pass

if count:
    print(f"  → Metadatos verificados en {count} archivo(s).")
PYEOF
}

# ═══════════════════════════════════════════════════════════════
#  COOKIES / AUTENTICACIÓN
# ═══════════════════════════════════════════════════════════════
cookie_label() {
    case "$COOKIE_BROWSER" in
        none) printf '%s' 'Desactivadas' ;;
        *) printf 'Navegador: %s' "$COOKIE_BROWSER" ;;
    esac
}

build_auth_args() {
    AUTH_ARGS=()
    [[ "$COOKIE_BROWSER" != "none" ]] && AUTH_ARGS+=( --cookies-from-browser "$COOKIE_BROWSER" )
}

browser_available() {
    case "$1" in
        firefox) command -v firefox &>/dev/null || [[ -d "$HOME/.mozilla/firefox" ]] ;;
        chrome) command -v google-chrome &>/dev/null || [[ -d "$HOME/.config/google-chrome" ]] || [[ -d "$HOME/.var/app/com.google.Chrome" ]] ;;
        chromium) command -v chromium &>/dev/null || [[ -d "$HOME/.config/chromium" ]] || [[ -d "$HOME/.var/app/org.chromium.Chromium" ]] ;;
        brave) command -v brave-browser &>/dev/null || [[ -d "$HOME/.config/BraveSoftware/Brave-Browser" ]] ;;
        edge) command -v microsoft-edge &>/dev/null || [[ -d "$HOME/.config/microsoft-edge" ]] ;;
        vivaldi) command -v vivaldi &>/dev/null || [[ -d "$HOME/.config/vivaldi" ]] ;;
        opera) command -v opera &>/dev/null || [[ -d "$HOME/.config/opera" ]] ;;
        *) return 1 ;;
    esac
}

menu_cookies() {
    clear
    echo -e "${BOLD}${CYAN}══ Cookies opcionales de navegador ══${NC}\n"
    echo -e "Las cookies pueden ayudar con inicios de sesión y verificaciones anti-bot." 
    echo -e "OtterRip ${BOLD}no guarda las cookies${NC}; yt-dlp las extrae del navegador elegido."
    echo ""
    echo -e "  ${CYAN}1)${NC} Desactivadas"
    echo -e "  ${CYAN}2)${NC} Firefox"
    echo -e "  ${CYAN}3)${NC} Chrome"
    echo -e "  ${CYAN}4)${NC} Chromium"
    echo -e "  ${CYAN}5)${NC} Brave"
    echo -e "  ${CYAN}6)${NC} Edge"
    echo -e "  ${CYAN}7)${NC} Vivaldi"
    echo -e "  ${CYAN}8)${NC} Opera"
    echo ""
    read -rp "  Opción (Enter=$(cookie_label)): " b
    [[ -z "$b" ]] && return
    case "$b" in
        1) COOKIE_BROWSER="none" ;;
        2) COOKIE_BROWSER="firefox" ;;
        3) COOKIE_BROWSER="chrome" ;;
        4) COOKIE_BROWSER="chromium" ;;
        5) COOKIE_BROWSER="brave" ;;
        6) COOKIE_BROWSER="edge" ;;
        7) COOKIE_BROWSER="vivaldi" ;;
        8) COOKIE_BROWSER="opera" ;;
        *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1; return ;;
    esac

    if [[ "$COOKIE_BROWSER" != "none" ]] && ! browser_available "$COOKIE_BROWSER"; then
        echo -e "  ${YELLOW}⚠${NC} No detecté claramente ese navegador, pero puedes probarlo igualmente."
    fi
    cfg_save
    echo -e "  ${GREEN}✔ Autenticación: $(cookie_label)${NC}"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  ERROR CLASSIFICATION / FAILURES
# ═══════════════════════════════════════════════════════════════
ensure_failed_header() {
    [[ -f "$FAILED_FILE" ]] || printf 'timestamp\tkind\tcategory\tid\ttitle\turl\tdestination\n' > "$FAILED_FILE"
}

classify_error() {
    local text="$1"
    local lower
    lower="${text,,}"
    if [[ "$lower" == *"sign in to confirm you’re not a bot"* || "$lower" == *"sign in to confirm you're not a bot"* || "$lower" == *"confirm you're not a bot"* ]]; then
        printf '%s' 'BOT_OR_AUTH'
    elif [[ "$lower" == *"video unavailable"* || "$lower" == *"video isn't available"* || "$lower" == *"this video is unavailable"* ]]; then
        printf '%s' 'UNAVAILABLE'
    elif [[ "$lower" == *"private video"* || "$lower" == *"video is private"* ]]; then
        printf '%s' 'PRIVATE'
    elif [[ "$lower" == *"members-only"* || "$lower" == *"members only"* ]]; then
        printf '%s' 'MEMBERS_ONLY'
    elif [[ "$lower" == *"not available in your country"* || "$lower" == *"geo restricted"* || "$lower" == *"geographical restriction"* ]]; then
        printf '%s' 'REGION'
    elif [[ "$lower" == *"age-restricted"* || "$lower" == *"age restricted"* ]]; then
        printf '%s' 'AGE_RESTRICTED'
    elif [[ "$lower" == *"login required"* || "$lower" == *"authentication"* ]]; then
        printf '%s' 'AUTH_REQUIRED'
    elif [[ "$lower" == *"http error 429"* || "$lower" == *"too many requests"* || "$lower" == *"rate limit"* ]]; then
        printf '%s' 'RATE_LIMIT'
    elif [[ "$lower" == *"http error 403"* || "$lower" == *"forbidden"* ]]; then
        printf '%s' 'FORBIDDEN'
    else
        printf '%s' 'OTHER'
    fi
}

record_failure() {
    local kind="$1" category="$2" id="$3" title="$4" url="$5" destination="$6"
    ensure_failed_header
    local stamp
    stamp="$(date '+%Y-%m-%d %H:%M:%S')"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$stamp" "$kind" "$category" "$id" \
        "$(safe_name "$title")" "$url" "$destination" >> "$FAILED_FILE"
}

failure_reason_from_log() {
    local id="$1" log="$2"
    [[ -f "$log" ]] || { printf '%s' 'OTHER'; return; }
    local line
    line="$(grep -F "$id" "$log" | grep -Ei 'ERROR:|WARNING:' | tail -n 1 || true)"
    classify_error "$line"
}

# ═══════════════════════════════════════════════════════════════
#  DETECCIÓN AUTOMÁTICA
# ═══════════════════════════════════════════════════════════════
detect_url_type() {
    local url="$1" json
    DETECTED_TYPE="song"
    DETECTED_TITLE=""
    DETECTED_COUNT=""

    build_auth_args

    # Primero intentamos URL como elemento único. Esto evita que una canción
    # que trae un parámetro &list= sea confundida con una playlist completa.
    json="$(yt-dlp "${AUTH_ARGS[@]}" --dump-single-json --skip-download --no-warnings --no-playlist "$url" 2>/dev/null || true)"

    if [[ -n "$json" ]]; then
        local detected_single
        detected_single="$(YT_JSON="$json" python3 - <<'PYEOF'
import json, os
raw = os.environ.get('YT_JSON', '').strip()
try:
    data = json.loads(raw)
except Exception:
    print('song\t\t')
    raise SystemExit
kind = str(data.get('_type') or 'video').lower()
title = str(data.get('title') or '')
print(('song' if kind != 'playlist' else 'playlist') + '\t' + title + '\t')
PYEOF
)"
        IFS=$'\t' read -r DETECTED_TYPE DETECTED_TITLE DETECTED_COUNT <<< "$detected_single"
        if [[ "$DETECTED_TYPE" == "song" ]]; then
            return 0
        fi
    fi

    # Si no era un elemento único, intentamos la URL como colección.
    json="$(yt-dlp "${AUTH_ARGS[@]}" --dump-single-json --skip-download --no-warnings --playlist-items 1 "$url" 2>/dev/null || true)"
    [[ -z "$json" ]] && return 1

    local detected
    detected="$(YT_JSON="$json" python3 - <<'PYEOF'
import json, os
raw = os.environ.get('YT_JSON', '').strip()
try:
    data = json.loads(raw)
except Exception:
    print('song\t\t')
    raise SystemExit

kind = str(data.get('_type') or 'video').lower()
playlist_id = str(data.get('playlist_id') or '')
playlist_title = str(data.get('playlist_title') or '')
album = str(data.get('album') or '')
title = str(data.get('title') or '')
web = str(data.get('webpage_url') or data.get('original_url') or '').lower()
domain = str(data.get('webpage_url_domain') or '').lower()
count = data.get('playlist_count') or data.get('n_entries') or ''

looks_album = (
    '/album/' in web or '/albums/' in web
    or (album and playlist_title and album.strip().casefold() == playlist_title.strip().casefold())
    or ('music.youtube.com' in domain and playlist_id.startswith('OLAK5uy_'))
)
collection = kind == 'playlist' or bool(playlist_id) or bool(playlist_title)
out_kind = 'album' if collection and looks_album else ('playlist' if collection else 'song')
out_title = playlist_title or album or title
print(f'{out_kind}\t{out_title}\t{count}')
PYEOF
)"

    IFS=$'\t' read -r DETECTED_TYPE DETECTED_TITLE DETECTED_COUNT <<< "$detected"
    [[ "$DETECTED_TYPE" =~ ^(song|playlist|album)$ ]] || DETECTED_TYPE="song"
    return 0
}

detected_type_label() {
    case "$1" in
        song) printf '%s' 'Canción individual' ;;
        playlist) printf '%s' 'Playlist' ;;
        album) printf '%s' 'Álbum' ;;
        *) printf '%s' 'Desconocido' ;;
    esac
}

# ═══════════════════════════════════════════════════════════════
#  INFO DE COLECCIÓN PARA REGISTRO/RESUMEN
# ═══════════════════════════════════════════════════════════════
get_collection_entries() {
    local url="$1" outfile="$2"
    build_auth_args
    yt-dlp "${AUTH_ARGS[@]}" --flat-playlist --print '%(id)s\t%(title)s\t%(duration)s\t%(webpage_url)s' \
        --no-warnings --ignore-errors "$url" > "$outfile" 2>/dev/null
}

# ═══════════════════════════════════════════════════════════════
#  DOWNLOAD COMMAND
# ═══════════════════════════════════════════════════════════════
build_common_cmd() {
    CMD=(
        yt-dlp
        "${AUTH_ARGS[@]}"
        --ignore-errors
        --continue
        --no-overwrites
        --retries "$MAX_RETRIES"
        --fragment-retries "$MAX_RETRIES"
        --extractor-retries 3
        --retry-sleep 'http:exp=1:20'
        --retry-sleep 'fragment:exp=1:15'
        --retry-sleep 'extractor:exp=2:30'
        --socket-timeout 30
        --sleep-requests 1
        --sleep-interval "$SLEEP_MIN"
        --max-sleep-interval "$SLEEP_MAX"
        --skip-unavailable-fragments
        -x
        --audio-format "$AFMT"
        --audio-quality "$AQ"
        --embed-metadata
        --add-metadata
        --parse-metadata '%(title)s:%(meta_title)s'
        --parse-metadata '%(artist,uploader)s:%(meta_artist)s'
        --parse-metadata '%(album)s:%(meta_album)s'
        --parse-metadata '%(release_year,upload_date>%Y)s:%(meta_date)s'
        --parse-metadata '%(genre)s:%(meta_genre)s'
        --parse-metadata '%(track_number,playlist_index)s:%(meta_track)s'
        --match-filter "duration > $DUR_MIN & duration <= $DUR_MAX"
        --embed-thumbnail
        --convert-thumbnails jpg
        --progress
        --newline
    )
    [[ -n "$PP_VALUE" ]] && CMD+=( --postprocessor-args "$PP_VALUE" )
    [[ "$AFMT" == "mp3" ]] && CMD+=( --postprocessor-args 'ffmpeg:-id3v2_version 3 -write_id3v1 1' )
    if [[ -n "$COVER_LOCAL" && -f "$COVER_LOCAL" ]]; then
        CMD+=( --ppa "EmbedThumbnail+ffmpeg_i1:${COVER_LOCAL}" )
    fi
}

# ═══════════════════════════════════════════════════════════════
#  DOWNLOAD ONE URL / COLLECTION
# ═══════════════════════════════════════════════════════════════
download_url() {
    local url="$1" forced_type="${2:-auto}" forced_dest="${3:-}" force_flat="${4:-0}"
    local type_mode="$forced_type"
    local title="" count=""
    local log timestamp

    RUN_SUCCESS=0; RUN_FILTERED=0; RUN_FAILED=0; RUN_SKIPPED=0
    RUN_BOT=0; RUN_UNAVAILABLE=0; RUN_OTHER=0

    echo -e "\n${YELLOW}🔎 Analizando URL...${NC}"
    if [[ "$type_mode" == "auto" ]]; then
        if ! detect_url_type "$url"; then
            echo -e "${RED}✘ No se pudo analizar la URL.${NC}"
            return 1
        fi
        type_mode="$DETECTED_TYPE"
        title="$DETECTED_TITLE"
        count="$DETECTED_COUNT"
    fi

    [[ -z "$title" ]] && title="$DETECTED_TITLE"
    [[ -z "$count" ]] && count="$DETECTED_COUNT"

    echo -e "  ${GREEN}✔ Detectado:${NC} ${BOLD}$(detected_type_label "$type_mode")${NC}"
    [[ -n "$title" ]] && echo -e "  ${BOLD}Nombre:${NC} $title"
    [[ -n "$count" ]] && echo -e "  ${BOLD}Elementos detectados:${NC} $count"
    echo -e "  ${BOLD}Cookies:${NC} $(cookie_label)"

    local folder="$forced_dest"
    if [[ -z "$folder" ]]; then
        echo ""
        read -rp "  Carpeta destino (Enter=Music): " folder
        [[ -z "$folder" ]] && folder="Music"
    fi
    folder="$(safe_name "$folder")"
    local DEST="$DOWNLOADS/$folder"
    mkdir -p "$DEST"

    build_auth_args

    case "$type_mode" in
        song)
            if [[ "$SONG_LAYOUT" == "artist" && "$force_flat" != "1" ]]; then
                OUTTPL="$DEST/%(uploader,artist,channel)s/%(title)s [%(id)s].%(ext)s"
            else
                OUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
            fi
            ;;
        playlist)
            OUTTPL="$DEST/%(playlist_title)s/%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"
            ;;
        album)
            if [[ "$ALBUM_LAYOUT" == "single" ]]; then
                OUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
            else
                OUTTPL="$DEST/%(album,playlist_title)s/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"
            fi
            ;;
        *) return 1 ;;
    esac

    local archive="$DEST/.otterrip_archive"
    local before_archive="$(mktemp)"
    local collection_info="$(mktemp)"
    LAST_RUN_LOG="$LOG_DIR/$(date '+%Y%m%d_%H%M%S')_${RANDOM}.log"
    ensure_failed_header

    # Snapshot archive antes de descargar: nos permite detectar elementos que
    # no terminaron correctamente sin marcar como fallido un elemento antiguo.
    touch "$archive"
    cp "$archive" "$before_archive"

    # Para colecciones intentamos obtener sus elementos para registrar fallidos.
    if [[ "$type_mode" != "song" ]]; then
        get_collection_entries "$url" "$collection_info" || :
    else
        printf '%s\t%s\t\t%s\n' \
            "$(yt-dlp "${AUTH_ARGS[@]}" --print '%(id)s' --no-warnings --skip-download --no-playlist "$url" 2>/dev/null || echo unknown)" \
            "$title" "" "$url" > "$collection_info"
    fi

    build_common_cmd
    CMD+=( --download-archive "$archive" )

    if [[ "$type_mode" == "song" ]]; then
        CMD+=( --no-playlist )
    else
        CMD+=( --yes-playlist )
    fi
    CMD+=( -o "$OUTTPL" "$url" )

    echo ""
    echo -e "${BOLD}${GREEN}══ Descargando ══${NC}"
    echo -e "  Perfil:     ${BOLD}$PROFILE${NC}"
    echo -e "  Duración:   ${BOLD}>${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
    echo -e "  Destino:    ${CYAN}$DEST${NC}"
    echo -e "  Cookies:    ${BOLD}$(cookie_label)${NC}"
    echo -e "  Pausas:     ${SLEEP_MIN}–${SLEEP_MAX}s entre solicitudes"
    echo -e "  Reintentos: ${MAX_RETRIES}"
    [[ "$type_mode" == "album" ]] && echo -e "  Álbum:      ${MAGENTA}$(album_layout_label)${NC}"
    [[ "$type_mode" == "song" ]] && echo -e "  Canciones:  ${MAGENTA}$(song_layout_label)${NC}"
    echo ""

    pause_adaptive
    "${CMD[@]}" 2>&1 | tee "$LAST_RUN_LOG"
    local status=${PIPESTATUS[0]}

    echo ""
    echo -e "  ${YELLOW}⚙${NC} Analizando resultado..."
    summarize_run "$type_mode" "$collection_info" "$before_archive" "$archive" "$LAST_RUN_LOG" "$DEST" "$title"

    echo -e "  ${YELLOW}⚙${NC} Reparando metadatos..."
    fix_metadata "$DEST" "$AFMT"

    rm -f "$before_archive" "$collection_info"

    if (( RUN_BOT > 0 )); then
        pause_after_bot
    fi

    if (( status == 0 )); then
        return 0
    fi
    return 1
}

# ═══════════════════════════════════════════════════════════════
#  RUN SUMMARY / FAILURE REGISTRATION
# ═══════════════════════════════════════════════════════════════
summarize_run() {
    local kind="$1" info="$2" before="$3" archive="$4" log="$5" destination="$6" collection_title="${7:-}"

    local total=0 success=0 failed=0 filtered=0 bot=0 unavailable=0 other=0
    declare -A before_ids=()
    if [[ -f "$before" ]]; then
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            before_ids["${line##* }"]=1
        done < "$before"
    fi

    # Leer candidatos de la colección. Si la detección de playlist falló,
    # todavía damos un resumen útil con el log y el archive.
    while IFS=$'\t' read -r id item_title duration item_url; do
        [[ -z "$id" ]] && continue
        ((total++))

        # ¿Ya estaba en el archive antes? Entonces no cuenta como nuevo.
        if [[ -n "${before_ids[$id]:-}" ]]; then
            ((success++))
            continue
        fi

        if grep -qE "(^|[[:space:]])${id}$" "$archive" 2>/dev/null; then
            ((success++))
            continue
        fi

        # Si duration es numérico, aplicar el mismo filtro para no registrar
        # como error las pistas que fueron correctamente rechazadas por duración.
        if [[ "$duration" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
            if awk -v d="$duration" -v mn="$DUR_MIN" -v mx="$DUR_MAX" 'BEGIN { exit !(d <= mn || d > mx) }'; then
                ((filtered++))
                continue
            fi
        fi

        local reason
        reason="$(failure_reason_from_log "$id" "$log")"
        case "$reason" in
            BOT_OR_AUTH|AUTH_REQUIRED) ((bot++));;
            UNAVAILABLE|PRIVATE|MEMBERS_ONLY|REGION|AGE_RESTRICTED) ((unavailable++));;
            *) ((other++));;
        esac
        ((failed++))
        local item_destination="$destination"
        if [[ "$kind" == "playlist" || ( "$kind" == "album" && "$ALBUM_LAYOUT" == "split" ) ]]; then
            item_destination="$destination/$(safe_name "$collection_title")"
            mkdir -p "$item_destination"
        fi
        record_failure "$kind" "$reason" "$id" "$item_title" "$item_url" "$item_destination"
    done < "$info"

    # Para un vídeo único, el extractor puede no haber dado info de colección.
    if (( total == 0 )); then
        if grep -qiE 'duration >|duration <|has a duration' "$log" 2>/dev/null; then
            filtered=1
        elif grep -qiE 'Sign in to confirm|confirm.*bot' "$log" 2>/dev/null; then
            bot=1; failed=1
        elif grep -qiE 'Video unavailable|Private video|not available in your country' "$log" 2>/dev/null; then
            unavailable=1; failed=1
        elif (( $? != 0 )); then
            other=1; failed=1
        fi
        total=1
        if (( failed == 0 && filtered == 0 )); then success=1; fi
    fi

    RUN_SUCCESS="$success"
    RUN_FILTERED="$filtered"
    RUN_FAILED="$failed"
    RUN_BOT="$bot"
    RUN_UNAVAILABLE="$unavailable"
    RUN_OTHER="$other"
    RUN_SKIPPED=0

    echo ""
    echo -e "${BOLD}${CYAN}══ Resumen ══${NC}"
    echo -e "  Total procesados:      ${BOLD}$total${NC}"
    echo -e "  ${GREEN}✔ Descargados:          $success${NC}"
    echo -e "  ${YELLOW}⏭ Omitidos por duración:$filtered${NC}"
    echo -e "  ${RED}✘ Fallidos:             $failed${NC}"
    echo -e "    🛡 Anti-bot/login:    $bot"
    echo -e "    ⚠ No disponibles:    $unavailable"
    echo -e "    ⚠ Otros:             $other"
    echo -e "  ${CYAN}Log:${NC} $log"
    if (( failed > 0 )); then
        echo -e "  ${YELLOW}Fallidos guardados en:${NC} $FAILED_FILE"
    fi
}

# ═══════════════════════════════════════════════════════════════
#  REINTENTAR FALLIDOS
# ═══════════════════════════════════════════════════════════════
retry_failed() {
    ensure_failed_header
    if [[ ! -s "$FAILED_FILE" ]] || [[ "$(wc -l < "$FAILED_FILE")" -le 1 ]]; then
        clear
        echo -e "${BOLD}${CYAN}══ Reintentar fallidos ══${NC}\n"
        echo -e "  ${GREEN}✔ No hay descargas fallidas pendientes.${NC}"
        sleep 2
        return
    fi

    clear
    echo -e "${BOLD}${CYAN}══ Reintentar fallidos ══${NC}\n"
    local count
    count=$(( $(wc -l < "$FAILED_FILE") - 1 ))
    echo -e "  Hay ${YELLOW}${BOLD}$count${NC} registro(s) pendientes."
    echo -e "  Se volverán a intentar usando la configuración actual."
    echo ""
    echo -e "  ${CYAN}1)${NC} Reintentar todos"
    echo -e "  ${CYAN}2)${NC} Limpiar registro"
    echo -e "  ${CYAN}3)${NC} Volver"
    echo ""
    read -rp "  Opción: " op
    case "$op" in
        2)
            : > "$FAILED_FILE"
            ensure_failed_header
            echo -e "  ${GREEN}✔ Registro limpiado.${NC}"
            sleep 1
            return
            ;;
        3) return ;;
        1) ;;
        *) return ;;
    esac

    local tmp kept=0
    tmp="$(mktemp)"
    ensure_failed_header
    tail -n +2 "$FAILED_FILE" > "$tmp"
    : > "$FAILED_FILE"
    ensure_failed_header

    while IFS=$'\t' read -r timestamp kind category id title url destination; do
        [[ -z "$url" ]] && continue
        echo ""
        echo -e "${BOLD}${MAGENTA}↻ Reintentando:${NC} $title"
        echo -e "  Categoría anterior: $category"
        if download_url "$url" "song" "$destination" 1; then
            echo -e "  ${GREEN}✔ Reintento terminado.${NC}"
        else
            # download_url ya registra los nuevos fallos.
            kept=$((kept + 1))
        fi
        pause_adaptive
    done < "$tmp"

    rm -f "$tmp"
    echo ""
    echo -e "${GREEN}${BOLD}✔ Reintentos finalizados.${NC}"
    echo -e "  Pendientes aún registrados: ${kept}"
    read -rp "  Enter para continuar..." _
}

# ═══════════════════════════════════════════════════════════════
#  MENÚS DE CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════
menu_quality() {
    clear
    echo -e "${BOLD}${CYAN}══ Calidad de audio ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} MP3 320 kbps"
    echo -e "  ${CYAN}2)${NC} M4A AAC 256 kbps"
    echo -e "  ${CYAN}3)${NC} M4A AAC 320 kbps"
    echo -e "  ${CYAN}4)${NC} FLAC 16-bit / 44.1 kHz"
    echo -e "  ${CYAN}5)${NC} FLAC 24-bit / 96 kHz"
    echo ""
    read -rp "  Opción: " q
    case "$q" in
        1) AFMT="mp3"; AQ="0"; PP_VALUE=""; PROFILE="MP3 320 kbps" ;;
        2) AFMT="m4a"; AQ="0"; PP_VALUE="ffmpeg:-c:a aac -b:a 256k"; PROFILE="M4A AAC 256 kbps" ;;
        3) AFMT="m4a"; AQ="0"; PP_VALUE="ffmpeg:-c:a aac -b:a 320k"; PROFILE="M4A AAC 320 kbps" ;;
        4) AFMT="flac"; AQ="0"; PP_VALUE="ffmpeg:-sample_fmt s16 -ar 44100"; PROFILE="FLAC 16-bit/44.1kHz" ;;
        5) AFMT="flac"; AQ="0"; PP_VALUE="ffmpeg:-sample_fmt s32 -ar 96000"; PROFILE="FLAC 24-bit/96kHz" ;;
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1; return ;;
    esac
    cfg_save
}

menu_duration() {
    clear
    echo -e "${BOLD}${CYAN}══ Límites de duración ══${NC}\n"
    echo -e "  Por defecto: ${GREEN}>60 segundos y ≤8:00 minutos${NC}"
    echo ""
    read -rp "  Mínimo en segundos (Enter=$DUR_MIN): " dmin
    read -rp "  Máximo en segundos (Enter=$DUR_MAX): " dmax
    [[ -z "$dmin" ]] && dmin="$DUR_MIN"
    [[ -z "$dmax" ]] && dmax="$DUR_MAX"
    if ! is_positive_int "$dmin" || ! is_positive_int "$dmax" || (( dmin >= dmax )); then
        echo -e "  ${RED}✘ Rango inválido.${NC}"
        sleep 2
        return
    fi
    DUR_MIN="$dmin"; DUR_MAX="$dmax"
    cfg_save
    echo -e "  ${GREEN}✔ Guardado: >${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
    sleep 1
}

menu_album_layout() {
    clear
    echo -e "${BOLD}${CYAN}══ Organización de álbumes ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} Todos los temas en una sola carpeta"
    echo -e "  ${CYAN}2)${NC} Una carpeta separada por álbum"
    echo ""
    read -rp "  Opción [1/2] (Enter=1): " a
    [[ -z "$a" ]] && a="1"
    case "$a" in
        1) ALBUM_LAYOUT="single" ;;
        2) ALBUM_LAYOUT="split" ;;
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1; return ;;
    esac
    cfg_save
}

album_layout_label() {
    [[ "$ALBUM_LAYOUT" == "split" ]] && printf '%s' 'Carpeta por álbum' || printf '%s' 'Una sola carpeta'
}

menu_song_layout() {
    clear
    echo -e "${BOLD}${CYAN}══ Organización de canciones individuales ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} Una sola carpeta"
    echo -e "  ${CYAN}2)${NC} Separar por artista"
    echo ""
    read -rp "  Opción [1/2] (Enter=2): " a
    [[ -z "$a" ]] && a="2"
    case "$a" in
        1) SONG_LAYOUT="flat" ;;
        2) SONG_LAYOUT="artist" ;;
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1; return ;;
    esac
    cfg_save
}

song_layout_label() {
    [[ "$SONG_LAYOUT" == "artist" ]] && printf '%s' 'Separadas por artista' || printf '%s' 'Una sola carpeta'
}

menu_pacing() {
    clear
    echo -e "${BOLD}${CYAN}══ Pausas y reintentos ══${NC}\n"
    echo -e "Pausa actual: ${SLEEP_MIN}–${SLEEP_MAX}s"
    echo -e "Reintentos actuales: $MAX_RETRIES"
    echo ""
    read -rp "  Pausa mínima (segundos, Enter=$SLEEP_MIN): " a
    read -rp "  Pausa máxima (segundos, Enter=$SLEEP_MAX): " b
    read -rp "  Reintentos por elemento (Enter=$MAX_RETRIES): " c
    [[ -z "$a" ]] && a="$SLEEP_MIN"
    [[ -z "$b" ]] && b="$SLEEP_MAX"
    [[ -z "$c" ]] && c="$MAX_RETRIES"
    if ! [[ "$a" =~ ^[0-9]+$ && "$b" =~ ^[0-9]+$ && "$c" =~ ^[0-9]+$ ]] || (( a > b || c < 1 )); then
        echo -e "  ${RED}✘ Valores inválidos.${NC}"
        sleep 2
        return
    fi
    SLEEP_MIN="$a"; SLEEP_MAX="$b"; MAX_RETRIES="$c"
    cfg_save
    echo -e "  ${GREEN}✔ Configuración guardada.${NC}"
    sleep 1
}

menu_cover() {
    clear
    echo -e "${BOLD}${CYAN}══ Portada manual ══${NC}\n"
    echo -e "Deja vacío para usar thumbnail automático."
    echo ""
    read -rp "  Ruta JPG/PNG: " raw_cover
    if [[ -z "$raw_cover" ]]; then
        COVER_LOCAL=""
    elif [[ -f "$raw_cover" ]]; then
        COVER_LOCAL="$(realpath "$raw_cover")"
    else
        echo -e "  ${RED}Archivo no encontrado.${NC}"
        sleep 1
        return
    fi
    cfg_save
}

menu_guide() {
    clear
    echo -e "${BOLD}${CYAN}══ Guía ══${NC}\n"
    echo -e "${BOLD}MP3 320${NC}       — Muy compatible."
    echo -e "${BOLD}M4A AAC 256/320${NC} — Buena calidad/tamaño."
    echo -e "${BOLD}FLAC 16/44.1${NC}  — Lossless tipo CD."
    echo -e "${BOLD}FLAC 24/96${NC}    — Hi-Res lossless."
    echo ""
    echo -e "${YELLOW}⚠${NC} La calidad final nunca puede superar la calidad de la fuente."
    echo -e "${YELLOW}⚠${NC} Las cookies ayudan con autenticación/controles de acceso, pero no garantizan superar todos los controles de YouTube."
    read -rp "  Enter para volver..." _
}

# ═══════════════════════════════════════════════════════════════
#  DESCARGA INTERACTIVA
# ═══════════════════════════════════════════════════════════════
menu_download() {
    while true; do
        clear
        echo -e "${BOLD}${CYAN}══ Nueva descarga ══${NC}\n"
        echo -e "Pega una URL; OtterRip detectará automáticamente el tipo."
        echo -e "Cookies: ${BOLD}$(cookie_label)${NC} | Duración: ${BOLD}>${DUR_MIN}s ≤${DUR_MAX}s${NC}"
        echo ""
        read -rp "  URL (Enter=volver): " url
        [[ -z "$url" ]] && return
        download_url "$url" "auto" "" || true
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar otra URL"
        echo -e "  ${CYAN}2)${NC} Volver al menú"
        echo -e "  ${CYAN}3)${NC} Reintentar fallidos"
        echo -e "  ${CYAN}4)${NC} Salir"
        echo ""
        read -rp "  Opción: " op
        case "$op" in
            1) ;;
            2) return ;;
            3) retry_failed ;;
            4) exit 0 ;;
            *) return ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  MENÚ PRINCIPAL
# ═══════════════════════════════════════════════════════════════
menu() {
    while true; do
        clear
        echo -e "${BOLD}${CYAN}"
        echo "  ╔══════════════════════════════════════════════════════╗"
        echo "  ║                    🦦 OTTER RIP v5                   ║"
        echo "  ╚══════════════════════════════════════════════════════╝${NC}"
        echo -e "  ${BOLD}Perfil:${NC}        $PROFILE"
        echo -e "  ${BOLD}Duración:${NC}      >${DUR_MIN}s y ≤${DUR_MAX}s"
        echo -e "  ${BOLD}Álbumes:${NC}       $(album_layout_label)"
        echo -e "  ${BOLD}Canciones:${NC}     $(song_layout_label)"
        echo -e "  ${BOLD}Cookies:${NC}       $(cookie_label)"
        echo -e "  ${BOLD}Pausas:${NC}        ${SLEEP_MIN}–${SLEEP_MAX}s"
        echo -e "  ${BOLD}Fallidos:${NC}      $(( $(wc -l < "$FAILED_FILE" 2>/dev/null || echo 1) - 1 )) pendiente(s)"
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar audio"
        echo -e "  ${CYAN}2)${NC} Configurar calidad"
        echo -e "  ${CYAN}3)${NC} Configurar duración"
        echo -e "  ${CYAN}4)${NC} Organización de álbumes"
        echo -e "  ${CYAN}5)${NC} Organización de canciones"
        echo -e "  ${CYAN}6)${NC} Cookies / navegador"
        echo -e "  ${CYAN}7)${NC} Pausas y reintentos"
        echo -e "  ${CYAN}8)${NC} Portada manual"
        echo -e "  ${CYAN}9)${NC} Reintentar fallidos"
        echo -e "  ${CYAN}10)${NC} Guía"
        echo -e "  ${CYAN}11)${NC} Abrir downloads"
        echo -e "  ${CYAN}12)${NC} Salir"
        echo ""
        read -rp "  Opción: " op
        case "$op" in
            1) menu_download ;;
            2) menu_quality ;;
            3) menu_duration ;;
            4) menu_album_layout ;;
            5) menu_song_layout ;;
            6) menu_cookies ;;
            7) menu_pacing ;;
            8) menu_cover ;;
            9) retry_failed ;;
            10) menu_guide ;;
            11) xdg-open "$DOWNLOADS" 2>/dev/null || thunar "$DOWNLOADS" 2>/dev/null || { echo -e "  ${CYAN}$DOWNLOADS${NC}"; sleep 1; } ;;
            12) echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  INICIO
# ═══════════════════════════════════════════════════════════════
ensure_failed_header
cfg_load
install_deps
cfg_save
menu

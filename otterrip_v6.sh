#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  OtterRip v5 — Descargador de audio · Arch Linux
#  + Descargas paralelas (job pool)
# ═══════════════════════════════════════════════════════════════
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS="$SCRIPT_DIR/downloads"
mkdir -p "$DOWNLOADS"

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
ALBUM_LAYOUT="single"
PARALLEL_JOBS=3      # workers concurrentes (1 = secuencial)

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
    s="${s//\\/_}"; s="${s//\//_}"
    printf '%s' "$s"
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
        echo -e "  ${YELLOW}⟳${NC}  Actualizando yt-dlp..."
        yt-dlp -U --quiet 2>/dev/null || true
    fi

    if ! python3 -c "import mutagen" 2>/dev/null; then
        echo -e "  ${YELLOW}⚙${NC} Instalando python-mutagen..."
        _pacman python-mutagen || _pip mutagen \
            || echo -e "  ${YELLOW}⚠${NC} python-mutagen no disponible"
    else
        echo -e "  ${GREEN}✔${NC} python-mutagen ya instalado"
    fi

    if ! command -v AtomicParsley &>/dev/null; then
        _pacman atomicparsley \
            && echo -e "  ${GREEN}✔${NC} AtomicParsley instalado" \
            || echo -e "  ${YELLOW}⚠${NC} AtomicParsley no disponible"
    else
        echo -e "  ${GREEN}✔${NC} AtomicParsley ya instalado"
    fi

    echo -e "\n${GREEN}${BOLD}  Dependencias listas.${NC}\n"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  POST-PROCESO DE METADATOS
# ═══════════════════════════════════════════════════════════════
fix_metadata() {
    local dir="$1" fmt="$2"
    python3 -c "import mutagen" 2>/dev/null || return 0

    python3 - "$dir" "$fmt" <<'PYEOF'
import sys, re
from pathlib import Path

try:
    from mutagen.mp3  import MP3
    from mutagen.id3  import ID3, TIT2, TPE1, ID3NoHeaderError
    from mutagen.flac import FLAC
    from mutagen.mp4  import MP4
except ImportError:
    sys.exit(0)

target = Path(sys.argv[1])
fmt = sys.argv[2]
PATTERN = re.compile(r'^(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+)$')
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
                m = PATTERN.match(str(title_tag))
                if m:
                    tags.setall("TPE1", [TPE1(encoding=3, text=m.group("artist"))])
                    tags.setall("TIT2", [TIT2(encoding=3, text=m.group("title"))])
            tags.save(str(fp), v2_version=3, v1=2)

        elif fmt == "flac":
            audio = FLAC(fp)
            artist = audio.get("artist", [""])[0]
            title = audio.get("title", [""])[0]
            if not artist and title:
                m = PATTERN.match(title)
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
                    m = PATTERN.match(title)
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
#  MENÚ PRINCIPAL
# ═══════════════════════════════════════════════════════════════
menu() {
    while true; do
        clear
        echo -e "${BOLD}${CYAN}"
        echo "  ╔══════════════════════════════════════════════════╗"
        echo "  ║                 🦦 OTTER RIP v5                 ║"
        echo "  ╚══════════════════════════════════════════════════╝${NC}"
        echo -e "  ${BOLD}Perfil:${NC}      $PROFILE"
        echo -e "  ${BOLD}Duración:${NC}    >${DUR_MIN}s y ≤ $((DUR_MAX/60))m $((DUR_MAX%60))s"
        echo -e "  ${BOLD}Paralelo:${NC}    ${PARALLEL_JOBS} worker(s) $( (( PARALLEL_JOBS > 1 )) && echo "(activo)" || echo "(secuencial)" )"
        echo -e "  ${BOLD}Álbum:${NC}       $(album_layout_label)"
        echo -e "  ${BOLD}Portada:${NC}     ${COVER_LOCAL:-AUTO (thumbnail)}"
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar audio"
        echo -e "  ${CYAN}2)${NC} Configurar calidad"
        echo -e "  ${CYAN}3)${NC} Configurar límites de duración"
        echo -e "  ${CYAN}4)${NC} Configurar descargas paralelas"
        echo -e "  ${CYAN}5)${NC} Configurar carpeta de álbum"
        echo -e "  ${CYAN}6)${NC} Configurar portada manual"
        echo -e "  ${CYAN}7)${NC} Guía de calidad"
        echo -e "  ${CYAN}8)${NC} Abrir carpeta downloads"
        echo -e "  ${CYAN}9)${NC} Salir"
        echo ""
        read -rp "  Opción: " op
        case "$op" in
            1) menu_download ;;
            2) menu_quality ;;
            3) menu_duration ;;
            4) menu_parallel ;;
            5) menu_album_layout ;;
            6) menu_cover ;;
            7) menu_guide ;;
            8) xdg-open "$DOWNLOADS" 2>/dev/null || thunar "$DOWNLOADS" 2>/dev/null || { echo -e "  ${CYAN}$DOWNLOADS${NC}"; sleep 1; } ;;
            9) echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
}

album_layout_label() {
    if [[ "$ALBUM_LAYOUT" == "single" ]]; then
        printf '%s' "Todos los temas en una sola carpeta"
    else
        printf '%s' "Una carpeta por álbum"
    fi
}

# ═══════════════════════════════════════════════════════════════
#  CALIDAD
# ═══════════════════════════════════════════════════════════════
menu_quality() {
    clear
    echo -e "${BOLD}${CYAN}══ Calidad de audio ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} MP3  320 kbps"
    echo -e "  ${CYAN}2)${NC} M4A  AAC 256 kbps"
    echo -e "  ${CYAN}3)${NC} M4A  AAC 320 kbps"
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
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1 ;;
    esac
}

# ═══════════════════════════════════════════════════════════════
#  DURACIÓN
# ═══════════════════════════════════════════════════════════════
menu_duration() {
    clear
    echo -e "${BOLD}${CYAN}══ Límites de duración ══${NC}\n"
    echo -e "  Solo se descargan audios que cumplan:"
    echo -e "  ${GREEN}duración > mínima${NC}  y  ${GREEN}duración ≤ máxima${NC}"
    echo ""
    echo -e "  Actual → mín: ${BOLD}${DUR_MIN}s${NC}   máx: ${BOLD}${DUR_MAX}s${NC}"
    echo ""
    read -rp "  Duración mínima en segundos (Enter=${DUR_MIN}): " dmin
    read -rp "  Duración máxima en segundos (Enter=${DUR_MAX}): " dmax
    [[ -z "$dmin" ]] && dmin="$DUR_MIN"
    [[ -z "$dmax" ]] && dmax="$DUR_MAX"
    if ! is_positive_int "$dmin" || ! is_positive_int "$dmax" || (( dmin >= dmax )); then
        echo -e "  ${RED}✘ Rango inválido.${NC}"; sleep 2; return
    fi
    DUR_MIN="$dmin"; DUR_MAX="$dmax"
    echo -e "\n  ${GREEN}✔ Rango: >${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  DESCARGAS PARALELAS  ← NUEVO
# ═══════════════════════════════════════════════════════════════
menu_parallel() {
    clear
    echo -e "${BOLD}${CYAN}══ Descargas paralelas ══${NC}\n"
    echo -e "  Cuántas canciones se descargan ${BOLD}al mismo tiempo${NC} en playlists/álbumes."
    echo -e "  Workers actuales: ${BOLD}${PARALLEL_JOBS}${NC}"
    echo ""
    echo -e "  ${CYAN}Recomendaciones:${NC}"
    echo -e "   1 → Secuencial (sin paralelismo, comportamiento clásico)"
    echo -e "   3 → Equilibrado  ${GREEN}← recomendado${NC}"
    echo -e "   5 → Rápido (más CPU/RAM/ancho de banda)"
    echo -e "   8 → Máximo (puede fallar si la fuente limita conexiones)"
    echo ""
    read -rp "  Número de workers [1-8] (Enter=${PARALLEL_JOBS}): " n
    if [[ -z "$n" ]]; then
        echo -e "  ${GREEN}Sin cambios.${NC}"
    elif [[ "$n" =~ ^[1-8]$ ]]; then
        PARALLEL_JOBS="$n"
        echo -e "  ${GREEN}✔ Workers: ${PARALLEL_JOBS}${NC}"
    else
        echo -e "  ${RED}Inválido. Elige entre 1 y 8.${NC}"
    fi
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  ÁLBUM LAYOUT
# ═══════════════════════════════════════════════════════════════
menu_album_layout() {
    clear
    echo -e "${BOLD}${CYAN}══ Organización de álbumes ══${NC}\n"
    echo -e "  ${CYAN}1)${NC} Todos los temas del álbum en UNA sola carpeta"
    echo -e "  ${CYAN}2)${NC} UNA carpeta separada por álbum"
    echo ""
    read -rp "  Opción [1/2] (Enter=1): " a
    [[ -z "$a" ]] && a="1"
    case "$a" in
        1) ALBUM_LAYOUT="single" ;;
        2) ALBUM_LAYOUT="split" ;;
        *) echo -e "  ${RED}Inválido.${NC}"; sleep 1; return ;;
    esac
    echo -e "  ${GREEN}✔ $(album_layout_label)${NC}"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  PORTADA
# ═══════════════════════════════════════════════════════════════
menu_cover() {
    clear
    echo -e "${BOLD}${CYAN}══ Portada manual ══${NC}\n"
    echo -e "  Deja vacío para usar thumbnail automático."
    echo ""
    read -rp "  Ruta JPG/PNG: " raw_cover
    if [[ -z "$raw_cover" ]]; then
        COVER_LOCAL=""; echo -e "  ${GREEN}Portada: AUTO${NC}"
    elif [[ -f "$raw_cover" ]]; then
        COVER_LOCAL="$(realpath "$raw_cover")"
        echo -e "  ${GREEN}✔ $COVER_LOCAL${NC}"
    else
        echo -e "  ${RED}Archivo no encontrado. Sin cambios.${NC}"
    fi
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  GUÍA
# ═══════════════════════════════════════════════════════════════
menu_guide() {
    clear
    echo -e "${BOLD}${CYAN}══ Guía de calidad ══${NC}\n"
    echo -e "  ${BOLD}MP3 320 kbps${NC}       — Muy compatible."
    echo -e "  ${BOLD}M4A AAC 256/320${NC}    — Buena relación calidad/tamaño."
    echo -e "  ${BOLD}FLAC 16/44.1${NC}       — Lossless tipo CD."
    echo -e "  ${BOLD}FLAC 24/96${NC}         — Hi-Res lossless."
    echo ""
    echo -e "  ${YELLOW}⚠${NC} La calidad real está limitada por la fuente."
    echo ""
    read -rp "  Enter para volver..." _
}

# ═══════════════════════════════════════════════════════════════
#  DETECCIÓN AUTOMÁTICA DE URL
# ═══════════════════════════════════════════════════════════════
detect_url_type() {
    local url="$1"
    local json

    DETECTED_TYPE="song"
    DETECTED_TITLE=""
    DETECTED_COUNT=""

    json="$(yt-dlp --dump-single-json --skip-download --no-warnings \
        --playlist-items 1 --ignore-errors --no-playlist "$url" 2>/dev/null || true)"

    if [[ -z "$json" ]]; then
        json="$(yt-dlp --dump-single-json --skip-download --no-warnings \
            --playlist-items 1 --ignore-errors "$url" 2>/dev/null || true)"
    fi

    [[ -z "$json" ]] && return 1

    local detected
    detected="$(YT_JSON="$json" python3 - <<'PYEOF'
import json, os, shlex

raw = os.environ.get('YT_JSON', '').strip()
try:
    data = json.loads(raw)
except Exception:
    print("DETECT_KIND='song'"); print("DETECT_TITLE=''"); print("DETECT_COUNT=''")
    raise SystemExit

kind = str(data.get('_type') or 'video').lower()
playlist_id = str(data.get('playlist_id') or '')
playlist_title = str(data.get('playlist_title') or '')
album = str(data.get('album') or '')
title = str(data.get('title') or '')
page_url = str(data.get('webpage_url') or data.get('original_url') or '')
domain = str(data.get('webpage_url_domain') or '').lower()
count = data.get('playlist_count') or data.get('n_entries') or ''

looks_album = (
    '/album/' in page_url.lower()
    or '/albums/' in page_url.lower()
    or (album and playlist_title and album.strip().casefold() == playlist_title.strip().casefold())
    or ('music.youtube.com' in domain and playlist_id.startswith('OLAK5uy_'))
)

is_collection = kind == 'playlist' or bool(playlist_id) or bool(playlist_title)
out_kind = 'album' if is_collection and looks_album else ('playlist' if is_collection else 'song')
out_title = playlist_title or album or title

print('DETECT_KIND=' + shlex.quote(out_kind))
print('DETECT_TITLE=' + shlex.quote(out_title))
print('DETECT_COUNT=' + shlex.quote(str(count)))
PYEOF
)"

    eval "$detected"
    DETECTED_TYPE="${DETECT_KIND:-song}"
    DETECTED_TITLE="${DETECT_TITLE:-}"
    DETECTED_COUNT="${DETECT_COUNT:-}"

    case "$DETECTED_TYPE" in
        song|playlist|album) return 0 ;;
        *) DETECTED_TYPE="song"; return 0 ;;
    esac
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
#  BARRA DE PROGRESO  (helper)
# ═══════════════════════════════════════════════════════════════
# Usa variables del scope llamador: done, total, ok, err
_pbar() {
    local pct=0 bar="" i
    (( total > 0 )) && pct=$(( done * 100 / total ))
    for (( i=0; i<25; i++ )); do
        (( i < pct / 4 )) && bar+="█" || bar+="░"
    done
    printf '\r  [%s] %3d%%  %d/%d  ✔ %d  ✘ %d   ' \
        "$bar" "$pct" "$done" "$total" "$ok" "$err"
}

# ═══════════════════════════════════════════════════════════════
#  DESCARGA
# ═══════════════════════════════════════════════════════════════
menu_download() {
    local seguir=true

    while $seguir; do
        clear
        echo -e "${BOLD}${CYAN}══ Nueva descarga ══${NC}\n"
        echo -e "  Pega la URL; OtterRip detecta automáticamente si es"
        echo -e "  ${BOLD}canción, playlist o álbum${NC}."
        echo ""
        read -rp "  URL: " url
        [[ -z "$url" ]] && return

        echo ""
        echo -e "  ${YELLOW}🔎 Analizando URL...${NC}"
        if ! detect_url_type "$url"; then
            echo -e "  ${RED}✘ No se pudo analizar la URL.${NC}"
            sleep 2; continue
        fi

        local type_mode="$DETECTED_TYPE"
        echo -e "  ${GREEN}✔ Detectado:${NC} ${BOLD}$(detected_type_label "$type_mode")${NC}"
        [[ -n "$DETECTED_TITLE" ]] && echo -e "  ${BOLD}Nombre:${NC} $DETECTED_TITLE"
        [[ -n "$DETECTED_COUNT" ]] && echo -e "  ${BOLD}Elementos:${NC} $DETECTED_COUNT"

        echo ""
        read -rp "  Carpeta destino (Enter=Music): " folder
        [[ -z "$folder" ]] && folder="Music"
        folder="$(safe_name "$folder")"
        local DEST="$DOWNLOADS/$folder"
        mkdir -p "$DEST"

        local ARCHIVE="$DEST/.otterrip_archive"

        # ── Templates de salida (modo secuencial) ──────────────
        local OUTTPL PLFLAG
        case "$type_mode" in
            song)
                PLFLAG="--no-playlist"
                OUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
                ;;
            playlist)
                PLFLAG="--yes-playlist"
                OUTTPL="$DEST/%(playlist_title)s/%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"
                ;;
            album)
                PLFLAG="--yes-playlist"
                if [[ "$ALBUM_LAYOUT" == "single" ]]; then
                    OUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
                else
                    OUTTPL="$DEST/%(album,playlist_title)s/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"
                fi
                ;;
        esac

        # ── Flags comunes que comparten CMD y WCMD ──────────────
        local -a COMMON_FLAGS=(
            --ignore-errors
            --continue
            --no-overwrites
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
            --add-metadata
            --parse-metadata "%(title)s:%(meta_title)s"
            --parse-metadata "%(artist,uploader)s:%(meta_artist)s"
            --parse-metadata "%(album)s:%(meta_album)s"
            --parse-metadata "%(release_year,upload_date>%Y)s:%(meta_date)s"
            --parse-metadata "%(genre)s:%(meta_genre)s"
            --parse-metadata "%(track_number,playlist_index)s:%(meta_track)s"
            --match-filter "duration > $DUR_MIN & duration <= $DUR_MAX"
            --embed-thumbnail
            --convert-thumbnails jpg
            --download-archive "$ARCHIVE"
        )
        [[ -n "$PP_VALUE" ]] && COMMON_FLAGS+=( --postprocessor-args "$PP_VALUE" )
        [[ "$AFMT" == "mp3" ]] && COMMON_FLAGS+=( --postprocessor-args "ffmpeg:-id3v2_version 3 -write_id3v1 1" )
        [[ -n "$COVER_LOCAL" && -f "$COVER_LOCAL" ]] \
            && COMMON_FLAGS+=( --ppa "EmbedThumbnail+ffmpeg_i1:${COVER_LOCAL}" )

        echo ""
        echo -e "${BOLD}${GREEN}  ══ Descargando ══${NC}"
        echo -e "  Perfil:   ${BOLD}$PROFILE${NC}"
        echo -e "  Duración: ${BOLD}>${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
        echo -e "  Destino:  ${CYAN}$DEST${NC}"

        # ════════════════════════════════════════════════════════
        #  MODO PARALELO  (playlist / álbum con PARALLEL_JOBS > 1)
        # ════════════════════════════════════════════════════════
        if (( PARALLEL_JOBS > 1 )) && [[ "$type_mode" != "song" ]]; then

            echo -e "  Modo:     ${MAGENTA}${BOLD}PARALELO — ${PARALLEL_JOBS} workers${NC}"
            echo ""
            echo -e "  ${YELLOW}⟳ Obteniendo lista de elementos...${NC}"

            # Obtener TODAS las URLs de la colección (rápido, sin descargar)
            local -a ITEM_URLS
            mapfile -t ITEM_URLS < <(
                yt-dlp \
                    --flat-playlist \
                    --print "%(url)s" \
                    --ignore-errors \
                    --quiet \
                    "$url" 2>/dev/null
            )

            local total=${#ITEM_URLS[@]}

            if (( total == 0 )); then
                echo -e "  ${YELLOW}⚠ Sin elementos (¿ya descargados o URL inválida?).${NC}"
                sleep 2; continue
            fi

            echo -e "  ${CYAN}${total} elemento(s) en cola${NC}\n"

            # Template para workers: sustituye %(playlist_title)s por el
            # nombre detectado, ya que los workers reciben URLs individuales
            local safe_title
            safe_title="$(safe_name "$DETECTED_TITLE")"
            local WOUTTPL
            if [[ -n "$safe_title" ]]; then
                WOUTTPL="$DEST/$safe_title/%(title)s [%(id)s].%(ext)s"
            else
                WOUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
            fi

            # CMD de cada worker: --no-playlist (1 video), sin --progress
            # Subshell hereda COMMON_FLAGS del scope actual vía fork()
            local -a WCMD=(
                yt-dlp
                --no-playlist
                "${COMMON_FLAGS[@]}"
                --no-progress
                --quiet
                -o "$WOUTTPL"
            )

            # ── Job pool ────────────────────────────────────────
            local running=0 done=0 ok=0 err=0

            for (( i=0; i<total; i++ )); do

                # Esperar un slot libre
                while (( running >= PARALLEL_JOBS )); do
                    if wait -n 2>/dev/null; then (( ok++ )); else
                        local wrc=$?
                        (( wrc == 127 )) && break   # sin jobs activos
                        (( err++ ))
                    fi
                    (( running-- )); (( done++ ))
                    _pbar
                done

                # Lanzar worker en subshell (hereda WCMD y ITEM_URLS)
                ( "${WCMD[@]}" "${ITEM_URLS[$i]}" ) &
                (( running++ ))
                _pbar
            done

            # Drenar workers restantes
            while (( running > 0 )); do
                if wait -n 2>/dev/null; then (( ok++ )); else
                    local wrc=$?
                    (( wrc == 127 )) && break
                    (( err++ ))
                fi
                (( running-- )); (( done++ ))
                _pbar
            done

            printf '\n'

        # ════════════════════════════════════════════════════════
        #  MODO SECUENCIAL  (canción individual o PARALLEL_JOBS=1)
        # ════════════════════════════════════════════════════════
        else
            echo -e "  Modo:     ${CYAN}Secuencial${NC}"
            [[ "$type_mode" == "album" ]] \
                && echo -e "  Álbum:    ${MAGENTA}$(album_layout_label)${NC}"
            echo -e "  ${YELLOW}Los elementos con error se omiten y yt-dlp continúa.${NC}"
            echo ""

            local -a CMD=(
                yt-dlp
                "${COMMON_FLAGS[@]}"
                "$PLFLAG"
                --progress
                --newline
                -o "$OUTTPL"
            )

            "${CMD[@]}" "$url"
        fi

        local status=$?

        # ── Post-proceso ────────────────────────────────────────
        echo ""
        echo -e "  ${YELLOW}⚙${NC}  Reparando metadatos..."
        fix_metadata "$DEST" "$AFMT"

        echo ""
        if (( status == 0 )); then
            echo -e "${GREEN}${BOLD}  ✔ Proceso finalizado correctamente.${NC}"
        else
            echo -e "${YELLOW}${BOLD}  ⚠ Proceso terminado con avisos/errores parciales.${NC}"
        fi
        echo -e "  Archivos en: ${CYAN}$DEST${NC}"
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar más"
        echo -e "  ${CYAN}2)${NC} Volver al menú principal"
        echo -e "  ${CYAN}3)${NC} Salir"
        echo ""
        read -rp "  Opción: " post_op
        case "$post_op" in
            1) seguir=true ;;
            3) echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
            *) seguir=false ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  INICIO
# ═══════════════════════════════════════════════════════════════
install_deps
menu

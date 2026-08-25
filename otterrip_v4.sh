#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  OtterRip v3 — Descargador de audio · Arch Linux
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
DUR_MIN=60       # el filtro usa duration > DUR_MIN → 60 exactos quedan fuera
DUR_MAX=480      # máximo 8:00 min
ALBUM_LAYOUT="single"   # single | split

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
    # Limpieza simple para nombres de carpetas creados con datos remotos.
    local s="$1"
    s="${s//\\/_}"
    s="${s//\//_}"
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
        echo "  ║                 🦦 OTTER RIP v3                 ║"
        echo "  ╚══════════════════════════════════════════════════╝${NC}"
        echo -e "  ${BOLD}Perfil:${NC}      $PROFILE"
        echo -e "  ${BOLD}Duración:${NC}    >${DUR_MIN}s y ≤ $((DUR_MAX/60))m $((DUR_MAX%60))s"
        echo -e "  ${BOLD}Álbum:${NC}       $(album_layout_label)"
        echo -e "  ${BOLD}Portada:${NC}     ${COVER_LOCAL:-AUTO (thumbnail)}"
        echo -e "  ${BOLD}Metadatos:${NC}   Automáticos + reparación post-descarga"
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar audio"
        echo -e "  ${CYAN}2)${NC} Configurar calidad"
        echo -e "  ${CYAN}3)${NC} Configurar límites de duración"
        echo -e "  ${CYAN}4)${NC} Configurar carpeta de álbum"
        echo -e "  ${CYAN}5)${NC} Configurar portada manual"
        echo -e "  ${CYAN}6)${NC} Guía de calidad"
        echo -e "  ${CYAN}7)${NC} Abrir carpeta downloads"
        echo -e "  ${CYAN}8)${NC} Salir"
        echo ""
        read -rp "  Opción: " op
        case "$op" in
            1) menu_download ;;
            2) menu_quality ;;
            3) menu_duration ;;
            4) menu_album_layout ;;
            5) menu_cover ;;
            6) menu_guide ;;
            7) xdg-open "$DOWNLOADS" 2>/dev/null || thunar "$DOWNLOADS" 2>/dev/null || { echo -e "  ${CYAN}$DOWNLOADS${NC}"; sleep 1; } ;;
            8) echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
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
    echo -e "  Solo se descargan audios que cumplan estrictamente:"
    echo -e "  ${GREEN}duración > mínima${NC}  y  ${GREEN}duración ≤ máxima${NC}."
    echo -e "  ${YELLOW}Por defecto: más de 60 s y máximo 8:00 min.${NC}"
    echo ""
    echo -e "  Actual → mín: ${BOLD}${DUR_MIN}s${NC}   máx: ${BOLD}${DUR_MAX}s${NC}"
    echo ""
    read -rp "  Duración mínima en segundos (Enter=${DUR_MIN}): " dmin
    read -rp "  Duración máxima en segundos (Enter=${DUR_MAX}): " dmax

    [[ -z "$dmin" ]] && dmin="$DUR_MIN"
    [[ -z "$dmax" ]] && dmax="$DUR_MAX"

    if ! is_positive_int "$dmin" || ! is_positive_int "$dmax" || (( dmin >= dmax )); then
        echo -e "  ${RED}✘ Rango inválido. Debe cumplirse 0 < mínima < máxima.${NC}"
        sleep 2
        return
    fi

    DUR_MIN="$dmin"
    DUR_MAX="$dmax"
    echo -e "\n  ${GREEN}✔ Rango guardado: >${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  ÁLBUM: UNA CARPETA / SEPARADO
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
        COVER_LOCAL=""
        echo -e "  ${GREEN}Portada: AUTO${NC}"
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
    echo -e "     Convertir un MP3 a FLAC no recupera calidad perdida."
    echo ""
    read -rp "  Enter para volver..." _
}

# ═══════════════════════════════════════════════════════════════
#  DETECCIÓN AUTOMÁTICA DE URL
# ═══════════════════════════════════════════════════════════════
detect_url_type() {
    # Salida global: DETECTED_TYPE, DETECTED_TITLE, DETECTED_COUNT
    # song | playlist | album
    local url="$1"
    local json

    DETECTED_TYPE="song"
    DETECTED_TITLE=""
    DETECTED_COUNT=""

    # Primer intento: tratar la URL como un recurso individual. Esto evita
    # interpretar accidentalmente una canción perteneciente a una playlist como colección.
    json="$(yt-dlp --dump-single-json --skip-download --no-warnings --playlist-items 1 --ignore-errors --no-playlist "$url" 2>/dev/null || true)"

    # Si no fue un recurso individual, intentar como playlist/álbum.
    if [[ -z "$json" ]]; then
        json="$(yt-dlp --dump-single-json --skip-download --no-warnings --playlist-items 1 --ignore-errors "$url" 2>/dev/null || true)"
    fi

    [[ -z "$json" ]] && return 1

    # Pasamos el JSON por variable de entorno para no mezclar stdin del script
    # Python con stdin de los datos.
    local detected
    detected="$(YT_JSON="$json" python3 - <<'PYEOF'
import json, os, shlex

raw = os.environ.get('YT_JSON', '').strip()
try:
    data = json.loads(raw)
except Exception:
    print("DETECT_KIND=song")
    print("DETECT_TITLE=''")
    print("DETECT_COUNT=''")
    raise SystemExit

kind = str(data.get('_type') or 'video').lower()
playlist_id = str(data.get('playlist_id') or '')
playlist_title = str(data.get('playlist_title') or '')
album = str(data.get('album') or '')
title = str(data.get('title') or '')
page_url = str(data.get('webpage_url') or data.get('original_url') or '')
domain = str(data.get('webpage_url_domain') or '').lower()
count = data.get('playlist_count') or data.get('n_entries') or ''

# Algunas plataformas representan los álbumes como playlists. No existe un
# campo universal "album" en yt-dlp, así que combinamos varias señales.
looks_album = (
    '/album/' in page_url.lower()
    or ('/albums/' in page_url.lower())
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

    # Aplicar las variables calculadas de forma controlada.
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
#  DESCARGA
# ═══════════════════════════════════════════════════════════════
menu_download() {
    local seguir=true

    while $seguir; do
        clear
        echo -e "${BOLD}${CYAN}══ Nueva descarga ══${NC}\n"
        echo -e "  Pega la URL y OtterRip intentará detectar automáticamente si es"
        echo -e "  ${BOLD}canción, playlist o álbum${NC}."
        echo ""
        read -rp "  URL: " url
        [[ -z "$url" ]] && return

        echo ""
        echo -e "  ${YELLOW}🔎 Analizando URL...${NC}"
        if ! detect_url_type "$url"; then
            echo -e "  ${RED}✘ No se pudo analizar la URL.${NC}"
            echo -e "  ${YELLOW}Puedes comprobar que yt-dlp pueda abrirla directamente.${NC}"
            sleep 2
            continue
        fi

        local type_mode="$DETECTED_TYPE"
        echo -e "  ${GREEN}✔ Detectado:${NC} ${BOLD}$(detected_type_label "$type_mode")${NC}"
        [[ -n "$DETECTED_TITLE" ]] && echo -e "  ${BOLD}Nombre:${NC} $DETECTED_TITLE"
        [[ -n "$DETECTED_COUNT" ]] && echo -e "  ${BOLD}Elementos detectados:${NC} $DETECTED_COUNT"

        echo ""
        read -rp "  Carpeta destino (Enter=Music): " folder
        [[ -z "$folder" ]] && folder="Music"
        folder="$(safe_name "$folder")"
        local DEST="$DOWNLOADS/$folder"
        mkdir -p "$DEST"

        local OUTTPL PLFLAG ARCHIVE
        case "$type_mode" in
            song)
                PLFLAG="--no-playlist"
                OUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
                ARCHIVE="$DEST/.otterrip_archive"
                ;;
            playlist)
                PLFLAG="--yes-playlist"
                # Cada playlist queda dentro de su propia carpeta.
                OUTTPL="$DEST/%(playlist_title)s/%(playlist_index)03d - %(title)s [%(id)s].%(ext)s"
                ARCHIVE="$DEST/.otterrip_archive"
                ;;
            album)
                PLFLAG="--yes-playlist"
                if [[ "$ALBUM_LAYOUT" == "single" ]]; then
                    OUTTPL="$DEST/%(title)s [%(id)s].%(ext)s"
                else
                    OUTTPL="$DEST/%(album,playlist_title)s/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"
                fi
                ARCHIVE="$DEST/.otterrip_archive"
                ;;
        esac

        local -a CMD=(
            yt-dlp
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

            # Más de 60 s y como máximo DUR_MAX.
            --match-filter "duration > $DUR_MIN & duration <= $DUR_MAX"

            --embed-thumbnail
            --convert-thumbnails jpg

            --download-archive "$ARCHIVE"
            "$PLFLAG"
            --progress
            --newline
            -o "$OUTTPL"
        )

        [[ -n "$PP_VALUE" ]] && CMD+=( --postprocessor-args "$PP_VALUE" )
        [[ "$AFMT" == "mp3" ]] && CMD+=( --postprocessor-args "ffmpeg:-id3v2_version 3 -write_id3v1 1" )

        # La portada manual se añade como archivo de entrada al embebedor.
        if [[ -n "$COVER_LOCAL" && -f "$COVER_LOCAL" ]]; then
            CMD+=( --ppa "EmbedThumbnail+ffmpeg_i1:${COVER_LOCAL}" )
        fi

        CMD+=( "$url" )

        echo ""
        echo -e "${BOLD}${GREEN}  ══ Descargando ══${NC}"
        echo -e "  Perfil: ${BOLD}$PROFILE${NC}"
        echo -e "  Duración: ${BOLD}>${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
        echo -e "  Destino: ${CYAN}$DEST${NC}"
        [[ "$type_mode" == "album" ]] && echo -e "  Álbum: ${MAGENTA}$(album_layout_label)${NC}"
        echo -e "  ${YELLOW}Los elementos con error se omiten y yt-dlp continúa con el resto.${NC}"
        echo ""

        "${CMD[@]}"
        local status=$?

        echo ""
        echo -e "  ${YELLOW}⚙${NC} Reparando metadatos..."
        fix_metadata "$DEST" "$AFMT"

        echo ""
        if (( status == 0 )); then
            echo -e "${GREEN}${BOLD}  ✔ Proceso finalizado correctamente.${NC}"
        else
            echo -e "${YELLOW}${BOLD}  ⚠ Proceso terminado con avisos/errores.${NC}"
            echo -e "  ${YELLOW}Los elementos que sí pudieron descargarse se conservaron.${NC}"
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

install_deps
menu

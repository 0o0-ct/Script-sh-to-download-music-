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
DUR_MIN=61       # estrictamente > 60 s
DUR_MAX=480      # 8:00 min
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
#  DESCARGA
# ═══════════════════════════════════════════════════════════════
menu_download() {
    local seguir=true

    while $seguir; do
        clear
        echo -e "${BOLD}${CYAN}══ Tipo de descarga ══${NC}\n"
        echo -e "  ${CYAN}1)${NC} Canción individual"
        echo -e "  ${CYAN}2)${NC} Playlist"
        echo -e "  ${CYAN}3)${NC} Álbum"
        echo ""
        read -rp "  Opción [1/2/3] (Enter=1): " type_mode
        [[ -z "$type_mode" ]] && type_mode="1"
        [[ ! "$type_mode" =~ ^[123]$ ]] && type_mode="1"

        echo ""
        read -rp "  Carpeta destino (Enter=Music): " folder
        [[ -z "$folder" ]] && folder="Music"
        folder="$(safe_name "$folder")"
        local DEST="$DOWNLOADS/$folder"
        mkdir -p "$DEST"

        echo ""
        read -rp "  URL: " url
        [[ -z "$url" ]] && return

        local OUTTPL PLFLAG ARCHIVE
        case "$type_mode" in
            1)
                PLFLAG="--no-playlist"
                OUTTPL="$DEST/%(title)s.%(ext)s"
                ARCHIVE="$DEST/.otterrip_archive"
                ;;
            2)
                PLFLAG="--yes-playlist"
                # La playlist queda junta dentro de su propia carpeta.
                OUTTPL="$DEST/%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s"
                ARCHIVE="$DEST/.otterrip_archive"
                ;;
            3)
                PLFLAG="--yes-playlist"
                if [[ "$ALBUM_LAYOUT" == "single" ]]; then
                    OUTTPL="$DEST/%(title)s.%(ext)s"
                else
                    OUTTPL="$DEST/%(album,playlist_title)s/%(title)s.%(ext)s"
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
        echo -e "  Duración: ${BOLD}>${DUR_MIN%?}s y ≤${DUR_MAX}s${NC}"
        echo -e "  Destino: ${CYAN}$DEST${NC}"
        [[ "$type_mode" == "3" ]] && echo -e "  Álbum: ${MAGENTA}$(album_layout_label)${NC}"
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

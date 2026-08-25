#!/usr/bin/env bash
# =============================================================
#  OtterRip v2  --  Descargador de audio  (Arch Linux)
# =============================================================

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

profile="MP3 320 kbps"
afmt="mp3"
aq="0"
flac_sample_fmt=""
flac_ar=""
cover_local=""
browser_cookies=""   # firefox | chromium | chrome | brave | edge | opera
YTDLP_BIN=""
FFMPEG_DIR=""

mkdir -p bin downloads temp

# =============================================================
header() {
    echo
    echo -e "${GREEN}${BOLD}  ====================================================="
    echo -e "    OTTER RIP v2  --  Descargador de audio"
    echo -e "  =====================================================${RESET}"
    echo
}

pause() {
    echo
    read -rp "  Presiona Enter para continuar..." _dummy
}

# =============================================================
#  DEPENDENCIAS
# =============================================================

check_deps() {
    header
    echo -e "  ${CYAN}[ Verificando dependencias... ]${RESET}"
    echo

    # yt-dlp
    if command -v yt-dlp &>/dev/null; then
        YTDLP_BIN="$(command -v yt-dlp)"
        echo -e "  > ${GREEN}[OK]${RESET} yt-dlp ($YTDLP_BIN)"
    elif [[ -x "$SCRIPT_DIR/bin/yt-dlp" ]]; then
        YTDLP_BIN="$SCRIPT_DIR/bin/yt-dlp"
        echo -e "  > ${GREEN}[OK]${RESET} yt-dlp (bin/yt-dlp)"
    else
        echo -e "  > Descargando yt-dlp..."
        if curl -fsSL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp" \
               -o "$SCRIPT_DIR/bin/yt-dlp" && chmod +x "$SCRIPT_DIR/bin/yt-dlp"; then
            YTDLP_BIN="$SCRIPT_DIR/bin/yt-dlp"
            echo -e "  > ${GREEN}[OK]${RESET} yt-dlp descargado."
        else
            echo -e "  > ${RED}[ERROR]${RESET} No se pudo descargar yt-dlp."
            echo -e "         sudo pacman -S yt-dlp"
            exit 1
        fi
    fi

    # ffmpeg
    if command -v ffmpeg &>/dev/null; then
        FFMPEG_DIR="$(dirname "$(command -v ffmpeg)")"
        echo -e "  > ${GREEN}[OK]${RESET} ffmpeg ($FFMPEG_DIR/ffmpeg)"
    elif [[ -x "$SCRIPT_DIR/bin/ffmpeg" ]]; then
        FFMPEG_DIR="$SCRIPT_DIR/bin"
        echo -e "  > ${GREEN}[OK]${RESET} ffmpeg (bin/ffmpeg)"
    else
        echo -e "  > ${RED}[ERROR]${RESET} ffmpeg no encontrado."
        echo -e "         sudo pacman -S ffmpeg"
        exit 1
    fi

    # python-mutagen
    if python3 -c "import mutagen" 2>/dev/null; then
        echo -e "  > ${GREEN}[OK]${RESET} python-mutagen"
    else
        echo -e "  > ${YELLOW}[WARN]${RESET} python-mutagen no encontrado — thumbnails no se incrustarán."
        echo -e "         sudo pacman -S python-mutagen"
    fi

    # deno
    if command -v deno &>/dev/null; then
        echo -e "  > ${GREEN}[OK]${RESET} deno ($(command -v deno))"
    else
        echo -e "  > ${YELLOW}[WARN]${RESET} deno no encontrado — YouTube puede fallar sin JS runtime."
        echo -e "         sudo pacman -S deno"
    fi

    echo
    echo -e "  ${GREEN}Dependencias listas.${RESET}"
    sleep 1
}

# =============================================================
#  MENU PRINCIPAL
# =============================================================

menu() {
    while true; do
        clear
        header
        echo -e "  Perfil activo  : ${YELLOW}${profile}${RESET}"
        echo -e "  Portada manual : ${YELLOW}${cover_local:-<ninguna>}${RESET}"
        echo -e "  Cookies        : ${YELLOW}${browser_cookies:-<ninguna>}${RESET}"
        echo -e "  Filtro         : Solo pistas 2-10 min, sin videoclips"
        echo
        echo "  ====================================================="
        echo "     1.  Descargar audio"
        echo "     2.  Configurar calidad"
        echo "     3.  Portada manual  (opcional)"
        echo "     4.  Cookies del navegador  (fix anti-bot)"
        echo "     5.  Guia de calidad"
        echo "     6.  Abrir carpeta downloads"
        echo "     7.  Salir"
        echo "  ====================================================="
        echo
        read -rp "  Opcion > " op
        case "${op:-}" in
            1) do_download ;;
            2) do_quality  ;;
            3) do_cover    ;;
            4) do_cookies  ;;
            5) do_guide    ;;
            6) xdg-open "downloads" &>/dev/null & ;;
            7) exit 0 ;;
        esac
    done
}

# =============================================================
#  CALIDAD
# =============================================================

do_quality() {
    clear
    header
    echo "  Selecciona el perfil de calidad:"
    echo
    echo "  ====================================================="
    echo "     1.  MP3  320 kbps           (universal)"
    echo "     2.  M4A  AAC 256 kbps       (excelente)"
    echo "     3.  M4A  AAC 320 kbps       (maximo AAC)"
    echo "     4.  FLAC 16-bit / 44.1 kHz  (calidad CD)"
    echo "     5.  FLAC 24-bit / 96 kHz    (Hi-Res)"
    echo "  ====================================================="
    echo
    read -rp "  Opcion > " q
    case "${q:-}" in
        1) afmt="mp3";  aq="0";    flac_sample_fmt=""; flac_ar="";      profile="MP3 320 kbps"        ;;
        2) afmt="m4a";  aq="256k"; flac_sample_fmt=""; flac_ar="";      profile="M4A AAC 256 kbps"    ;;
        3) afmt="m4a";  aq="320k"; flac_sample_fmt=""; flac_ar="";      profile="M4A AAC 320 kbps"    ;;
        4) afmt="flac"; aq="0";    flac_sample_fmt="s16"; flac_ar="44100"; profile="FLAC 16-bit/44.1kHz" ;;
        5) afmt="flac"; aq="0";    flac_sample_fmt="s32"; flac_ar="96000"; profile="FLAC 24-bit/96kHz"   ;;
    esac
}

# =============================================================
#  PORTADA MANUAL
# =============================================================

do_cover() {
    clear
    header
    echo "  Ingresa la ruta completa a tu imagen JPG o PNG."
    echo "  Deja vacio para usar el thumbnail automatico."
    echo
    read -rp "  Ruta > " cover_local
}

# =============================================================
#  COOKIES
# =============================================================

do_cookies() {
    clear
    header
    echo "  YouTube a veces bloquea descargas pidiendo verificacion."
    echo "  Pasarle las cookies de tu navegador soluciona el problema."
    echo
    echo "  ====================================================="
    echo "     1.  Firefox"
    echo "     2.  Chromium"
    echo "     3.  Chrome"
    echo "     4.  Brave"
    echo "     5.  Sin cookies  (desactivar)"
    echo "  ====================================================="
    echo
    read -rp "  Opcion > " bc
    case "${bc:-}" in
        1) browser_cookies="firefox"  ;;
        2) browser_cookies="chromium" ;;
        3) browser_cookies="chrome"   ;;
        4) browser_cookies="brave"    ;;
        5) browser_cookies=""         ;;
    esac
}

# =============================================================
#  GUIA
# =============================================================

do_guide() {
    clear
    header
    echo "  GUIA DE CALIDAD"
    echo "  -------------------------------------------------------"
    echo "   MP3  320 kbps       : Maximo MP3. Compatible con todo."
    echo "   M4A  AAC 256 kbps   : Excelente calidad, menor tamano."
    echo "   M4A  AAC 320 kbps   : Maximo AAC."
    echo "   FLAC 16-bit/44.1kHz : Calidad CD sin perdida."
    echo "   FLAC 24-bit/96kHz   : Hi-Res, archivos grandes."
    echo "  -------------------------------------------------------"
    echo "   FILTRO: Solo pistas entre 2 y 10 minutos."
    echo "   No se descargan videoclips."
    echo "  -------------------------------------------------------"
    echo "   ANTI-BOT: Si YouTube da errores de 'Sign in',"
    echo "   ve a opcion 4 del menu y selecciona tu navegador."
    echo "  -------------------------------------------------------"
    echo
    pause
}

# =============================================================
#  DESCARGA
# =============================================================

do_download() {
    clear
    header
    echo "  Tipo de descarga:"
    echo
    echo "  ====================================================="
    echo "     1.  Cancion individual"
    echo "     2.  Playlist   (carpeta plana)"
    echo "     3.  Album      (carpeta plana)"
    echo "  ====================================================="
    echo
    read -rp "  Opcion [1/2/3] (Enter=1) > " type_mode
    [[ -z "${type_mode:-}" ]] && type_mode="1"
    [[ "$type_mode" != "1" && "$type_mode" != "2" && "$type_mode" != "3" ]] && type_mode="1"

    echo
    read -rp "  Carpeta destino en downloads (Enter=Music) > " folder
    [[ -z "${folder:-}" ]] && folder="Music"
    mkdir -p "downloads/${folder}"

    echo
    read -rp "  URL > " url
    [[ -z "${url:-}" ]] && return

    # --- Plantilla de salida ---
    case "$type_mode" in
        1) outtpl="downloads/${folder}/%(title)s.%(ext)s"
           plflag="--no-playlist" ;;
        2) outtpl="downloads/${folder}/%(playlist_index)02d - %(title)s.%(ext)s"
           plflag="--yes-playlist" ;;
        3) outtpl="downloads/${folder}/%(track_number,playlist_index)02d - %(title)s.%(ext)s"
           plflag="--yes-playlist" ;;
    esac

    # --- Construir el comando como array ---
    # El filtro va como argumento directo del array: bash maneja las comillas
    # correctamente, sin archivos de config intermedios que puedan romperse.
    cmd=(
        "$YTDLP_BIN"
        --ffmpeg-location "$FFMPEG_DIR"

        # Filtro de duracion y titulo (directo, sin config file)
        --match-filter
        "duration >= 120 & duration <= 600 & title !*= 'official video' & title !*= 'video oficial' & title !*= 'music video' & title !*= 'videoclip' & title !*= 'official music video'"

        # Delay anti-ratelimit
        --sleep-interval 3
        --max-sleep-interval 6

        # Audio
        -x
        --audio-format "$afmt"
        --audio-quality "$aq"

        # Tags incrustados en el archivo
        --embed-metadata
        --parse-metadata "title:%(title)s"
        --parse-metadata "artist:%(artist,uploader)s"
        --parse-metadata "album:%(album,playlist_title,playlist)s"
        --parse-metadata "track:%(track_number,playlist_index)s"
        --parse-metadata "date:%(release_date,upload_date)s"

        # Thumbnail
        --embed-thumbnail

        # Nombres de archivo seguros (sin subcarpetas accidentales)
        --windows-filenames

        # Playlist
        $plflag

        --no-overwrites
        --ignore-errors
        -o "$outtpl"
    )

    # Portada manual
    if [[ -n "${cover_local:-}" && -f "$cover_local" ]]; then
        cmd+=(--postprocessor-args "EmbedThumbnail+ffmpeg_i:-i ${cover_local}")
    fi

    # Cookies del navegador (fix anti-bot de YouTube)
    if [[ -n "${browser_cookies:-}" ]]; then
        cmd+=(--cookies-from-browser "$browser_cookies")
    fi

    # FLAC: argumentos extra de postprocesado
    if [[ "$afmt" == "flac" ]]; then
        cmd+=(--postprocessor-args "ffmpeg:-sample_fmt ${flac_sample_fmt} -ar ${flac_ar}")
    fi

    # URL al final
    cmd+=("$url")

    echo
    echo "  ====================================================="
    echo -e "   Iniciando descarga..."
    echo -e "   Perfil  : ${YELLOW}${profile}${RESET}"
    echo -e "   Destino : downloads/${folder}"
    echo    "   Filtro  : 2-10 min, sin videoclips"
    echo    "   Delay   : 3-6 s entre pistas"
    if [[ -n "${browser_cookies:-}" ]]; then
        echo -e "   Cookies : ${GREEN}${browser_cookies}${RESET}"
    fi
    echo "  ====================================================="
    echo

    "${cmd[@]}"

    echo
    echo "  ====================================================="
    echo -e "   ${GREEN}Descarga finalizada.${RESET}  Carpeta: downloads/${folder}"
    echo "  ====================================================="
    echo
    pause
}

# =============================================================
check_deps
menu

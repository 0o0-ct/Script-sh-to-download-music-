#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  OtterRip v6.1 — Descargador de audio + Letras (LRCLib API)
#  + Descargas paralelas (job pool)
#  + Búsqueda e incrustación de letras vía lrclib.net
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
FETCH_LYRICS=true    # Activar/desactivar búsqueda de letras
LYRICS_MODE="embed"  # 'embed' (en el archivo) o 'file' (.lrc aparte)
LRCLIB_HOST="https://lrclib.net" # Servidor API de LRCLIB (oficial o local http://localhost:3300)

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

    # Necesario para leer/escribir letras y metadatos avanzados
    if ! python3 -c "import mutagen; import requests; import bs4; import rapidfuzz" 2>/dev/null; then
        echo -e "  ${YELLOW}⚙${NC} Instalando librerías Python (mutagen, requests, bs4, rapidfuzz)..."
        _pip "mutagen requests beautifulsoup4 rapidfuzz" \
            || { echo -e "  ${RED}✘ Error instalando librerías Python${NC}"; exit 1; }
    else
        echo -e "  ${GREEN}✔${NC} Librerías Python listas"
    fi

    if ! command -v AtomicParsley &>/dev/null; then
        _pacman atomicparsley \
            && echo -e "  ${GREEN}✔${NC} AtomicParsley instalado" \
            || echo -e "  ${YELLOW}⚠${NC} AtomicParsley no disponible (M4A limitado)"
    else
        echo -e "  ${GREEN}✔${NC} AtomicParsley ya instalado"
    fi

    echo -e "\n${GREEN}${BOLD}  Dependencias listas.${NC}\n"
    sleep 1
}

# ═══════════════════════════════════════════════════════════════
#  GESTIÓN DE LETRAS (API LRCLIB.NET)
# ═══════════════════════════════════════════════════════════════
process_lyrics() {
    local dir="$1" fmt="$2" session_file="$3"
    
    # Solo ejecutar si está activado
    [[ "$FETCH_LYRICS" != "true" ]] && return 0

    # Verificar dependencias antes de lanzar el script Python
    if ! python3 -c "import mutagen; import requests" 2>/dev/null; then
        echo -e "  ${RED}✘ Error: Faltan librerías Python (mutagen/requests). Ejecuta: pip install mutagen requests${NC}"
        return 1
    fi

    local SYNCED_DIR="$SCRIPT_DIR/letras-webs-buscar"

    python3 - "$dir" "$fmt" "$LYRICS_MODE" "$LRCLIB_HOST" "$SYNCED_DIR" "$session_file" <<'PYEOF'
import sys, os, re, time
from pathlib import Path
from urllib.parse import quote

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, USLT, SYLT, ID3NoHeaderError
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    import requests
except ImportError as e:
    print(f"  Error importando librerías: {e}")
    sys.exit(1)

# ANSI Colors para feedback visual en terminal
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
MAGENTA = '\033[0;35m'
RED = '\033[0;31m'
BOLD = '\033[1m'
NC = '\033[0m'

target = Path(sys.argv[1])
fmt = sys.argv[2]
mode = sys.argv[3] # 'embed' or 'file'
api_host = sys.argv[4].rstrip('/') if len(sys.argv) > 4 and sys.argv[4] else "https://lrclib.net"
synced_dir = sys.argv[5] if len(sys.argv) > 5 else ""
session_file = sys.argv[6] if len(sys.argv) > 6 else ""

# Integración del metabuscador multired (letras-webs-buscar)
if synced_dir and os.path.isdir(synced_dir):
    sys.path.insert(0, synced_dir)

try:
    import syncedlyrics
    HAS_SYNCEDLYRICS = True
except Exception:
    HAS_SYNCEDLYRICS = False

API_GET = f"{api_host}/api/get"
API_SEARCH = f"{api_host}/api/search"
EXT = {"mp3": ".mp3", "flac": ".flac", "m4a": ".m4a"}.get(fmt, f".{fmt}")

# Sesión HTTP reutilizable con User-Agent oficial requerido por LRCLIB
session = requests.Session()
session.headers.update({
    "User-Agent": "OtterRip/6.1 (https://github.com/c0o0c/OtterRip; contact=music-app)",
    "Accept": "application/json"
})

def safe_request(url, params):
    """Ejecuta petición HTTP respetando límites de tasa (Rate Limiting)"""
    for attempt in range(2):
        try:
            resp = session.get(url, params=params, timeout=8)
            if resp.status_code == 429:
                retry_after = 2.0
                try:
                    retry_after = float(resp.headers.get("Retry-After", 2.0))
                except Exception:
                    pass
                print(f"       {YELLOW}⏳ Rate limit alcanzado. Pausando {retry_after:.1f}s...{NC}")
                time.sleep(retry_after)
                continue
            return resp
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return None

def extract_primary_artist(raw_artist):
    """Extrae únicamente el artista principal si existen múltiples colaboradores"""
    if not raw_artist:
        return ""
    clean = re.sub(r'\[.*?\]|\(.*?\)', '', raw_artist).strip()
    # Separadores de colaboradores: feat., ft., featuring, with, &, x, X, y, coma, punto y coma, barra
    parts = re.split(
        r'(?i:\s+(?:feat\.?|ft\.?|featuring|with)\s+|\s*[,;/]\s*|\s+&\s+|\s+[xX]\s+|\s+y\s+)',
        clean
    )
    if parts and parts[0].strip():
        return parts[0].strip()
    return clean

def extract_clean_track(raw_title, primary_artist=""):
    """Extrae únicamente el nombre de la canción sin colaboradores ni etiquetas de video"""
    if not raw_title:
        return ""
    s = raw_title
    # Quitar identificadores de video de YouTube [xxxxxxxxxxx]
    s = re.sub(r'\s*\[[a-zA-Z0-9_-]{11}\]\s*$', '', s)
    # Quitar etiquetas de video y colaboraciones del título
    noise_patterns = [
        r'\[(?i:(?:official|video|music video|audio|lyric video|lyrics|hd|4k|remastered|explicit|visualizer|clip|videoclip)).*?\]',
        r'\((?i:(?:official|video|music video|audio|lyric video|lyrics|hd|4k|remastered|explicit|visualizer|clip|videoclip oficial)).*?\)',
        r'[\(\[]\s*(?i:feat\.?|ft\.?|featuring|with)\s+[^)\]]+[\)\]]',
        r'(?i:\s+(?:feat\.?|ft\.?|featuring|with)\s+[^\-\(\]]*?)(?=\s*[-–—\(\[]|$)',
        r'(?i:\s*[-–—]\s*(?:musica catolica|música católica|audio oficial|video oficial|en vivo).*$)',
        r'(?i:\s+(?:musica catolica|música católica|audio oficial|video oficial).*$)'
    ]
    for p in noise_patterns:
        s = re.sub(p, '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    # Si el título aún conserva estructura "Artista - Canción", extraer solo la canción
    if " - " in s:
        parts = [p.strip() for p in s.split(" - ") if p.strip()]
        if len(parts) >= 2:
            if primary_artist and primary_artist.lower() in parts[0].lower():
                s = parts[1]
            elif not primary_artist:
                s = parts[1]
    # Limpieza final de descriptores comunes de canales/género
    s = re.sub(r'(?i:\s*[-–—]?\s*m[uú]sica cat[oó]lica.*$)', '', s).strip()
    return s.strip()

def fetch_lyrics_hybrid(primary_artist, clean_track, duration=None):
    """
    Búsqueda híbrida en cascada para canciones, playlists y álbumes:
    1. LRCLIB (Servidor oficial o local): búsqueda exacta y validación de duración.
    2. syncedlyrics (Musixmatch, NetEase, Megalobiz): catálogo global multired.
    3. Fallback a texto plano (Genius/Musixmatch) si no existe versión karaoke.
    """
    search_query = f"{primary_artist} {clean_track}".strip()

    # ── Nivel 1: LRCLIB ───────────────────────────────────────
    try:
        params = {
            "artist_name": primary_artist,
            "track_name": clean_track
        }
        if duration and isinstance(duration, (int, float)) and duration > 0:
            params["duration"] = int(round(duration))

        # Fase 1: /api/get exacto con firma de audio y duración
        resp = safe_request(API_GET, params)
        if resp and resp.status_code == 200:
            data = resp.json()
            synced = data.get("syncedLyrics") or data.get("synchronizedLyrics")
            if synced and "[00:" in synced:
                return synced, True, "LRCLIB"

        # Fase 2: /api/search en LRCLIB
        search_params = {"track_name": clean_track, "artist_name": primary_artist}
        resp_s = safe_request(API_SEARCH, search_params)
        if not resp_s or resp_s.status_code != 200 or not resp_s.json():
            resp_s = safe_request(API_SEARCH, {"q": search_query})

        if resp_s and resp_s.status_code == 200:
            results = resp_s.json()
            if isinstance(results, list):
                synced_candidates = [
                    r for r in results
                    if (r.get("syncedLyrics") or r.get("synchronizedLyrics")) and "[00:" in (r.get("syncedLyrics") or r.get("synchronizedLyrics"))
                ]
                if synced_candidates:
                    best = None
                    if duration and duration > 0:
                        close = [
                            c for c in synced_candidates
                            if c.get("duration") and abs(c["duration"] - duration) <= 4.5
                        ]
                        if close:
                            best = min(close, key=lambda c: abs(c["duration"] - duration))
                    if not best:
                        best = synced_candidates[0]

                    synced = best.get("syncedLyrics") or best.get("synchronizedLyrics")
                    return synced, True, "LRCLIB"
    except Exception:
        pass

    # ── Nivel 2: Multibuscador syncedlyrics (Musixmatch, NetEase, Megalobiz) ─
    if HAS_SYNCEDLYRICS:
        try:
            lrc_synced = syncedlyrics.search(search_query, synced_only=True)
            if lrc_synced and "[00:" in lrc_synced:
                return lrc_synced, True, "Multi-Web (Musixmatch/NetEase)"
        except Exception:
            pass

        # ── Nivel 3: Respaldo en texto plano (Genius/Musixmatch) ──
        try:
            lrc_plain = syncedlyrics.search(search_query, plain_only=True)
            if lrc_plain and lrc_plain.strip():
                return lrc_plain.strip(), False, "Genius/Musixmatch (Texto)"
        except Exception:
            pass

    return None, False, ""

def parse_lrc_for_sylt(lrc_text):
    """Convierte texto LRC en tuplas (texto, ms) para la trama binaria SYLT de ID3"""
    entries = []
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    for line in lrc_text.splitlines():
        m = pattern.match(line.strip())
        if m:
            minutes = int(m.group(1))
            seconds = float(m.group(2))
            ms = int((minutes * 60 + seconds) * 1000)
            txt = m.group(3).strip()
            entries.append((txt, ms))
    return entries

def save_lyrics_to_file(audio_path, text, is_synced=False):
    ext = ".lrc" if is_synced and "[00:" in text else ".txt"
    txt_path = audio_path.with_suffix(ext)
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(text)
    return txt_path

def embed_lyrics_mp3(path, text, is_synced=False):
    try:
        audio = MP3(path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(path)
        audio.add_tags()
    
    if not audio.tags:
        audio.add_tags()

    # Guardar en USLT con marcas de tiempo (compatible con la gran mayoría de reproductores)
    frame_uslt = USLT(encoding=3, lang='eng', desc='', text=text)
    audio.tags.setall('USLT', [frame_uslt])

    # Si es sincronizada, guardar también trama nativa SYLT de ID3v2
    if is_synced:
        sylt_entries = parse_lrc_for_sylt(text)
        if sylt_entries:
            try:
                frame_sylt = SYLT(encoding=3, lang='eng', format=1, type=1, desc='Lyrics', text=sylt_entries)
                audio.tags.setall('SYLT', [frame_sylt])
            except Exception:
                pass
    
    audio.save(v2_version=3)

def embed_lyrics_flac(path, text, is_synced=False):
    audio = FLAC(path)
    audio['LYRICS'] = text
    audio.save()

def embed_lyrics_m4a(path, text, is_synced=False):
    audio = MP4(path)
    if not audio.tags:
        audio.add_tags()
    audio.tags['\xa9lyr'] = text
    audio.save()

# Escaneo de archivos de audio: si se proporciona la sesión actual, procesar solo las recién descargadas
audio_files = []
if session_file and os.path.isfile(session_file):
    with open(session_file, 'r', encoding='utf-8', errors='ignore') as sf:
        for line in sf:
            raw_p = line.strip()
            if raw_p:
                p = Path(raw_p)
                if p.is_file() and p.suffix.lower() == EXT.lower() and p not in audio_files:
                    audio_files.append(p)
else:
    if target.is_file():
        audio_files = [target] if target.suffix.lower() == EXT.lower() else []
    elif target.is_dir():
        audio_files = sorted([f for f in target.rglob("*") if f.is_file() and f.suffix.lower() == EXT.lower()])

total = len(audio_files)

if total == 0:
    print(f"  {YELLOW}→ No se encontraron pistas nuevas ({EXT}) para procesar.{NC}")
    sys.exit(0)

print(f"\n{BOLD}{CYAN}══ Búsqueda e Incrustación Automática de Letras (1 a 1) ══{NC}")
print(f"  {BOLD}Servidor Principal:{NC} {api_host}")
if HAS_SYNCEDLYRICS:
    print(f"  {BOLD}Metabuscador:{NC}       Multi-Web activo (Musixmatch, NetEase, Megalobiz, Genius)")
print(f"  {BOLD}Pistas a procesar:{NC}  {total}\n")

count_already = 0
count_processed = 0
count_notfound = 0

for idx, fp in enumerate(audio_files, 1):
    try:
        artist = ""
        title = str(fp.stem)
        duration = 0
        has_lyrics_already = False

        # Parsear nombre de archivo base
        clean_title = title
        if " - " in title:
            parts = title.split(" - ", 1)
            artist = parts[0].strip()
            clean_title = parts[1].strip()

        # Extraer metadatos reales según formato
        try:
            if fmt == "mp3":
                t = ID3(fp)
                if t.get("TPE1"): artist = str(t["TPE1"])
                if t.get("TIT2"): clean_title = str(t["TIT2"])
                if t.get("SYLT") or (t.get("USLT") and "[00:" in str(t.get("USLT"))):
                    has_lyrics_already = True
                audio_obj = MP3(fp)
                duration = audio_obj.info.length
            elif fmt == "flac":
                t = FLAC(fp)
                if t.get("artist"): artist = t["artist"][0]
                if t.get("title"): clean_title = t["title"][0]
                if t.get("lyrics") and "[00:" in str(t.get("lyrics")[0]):
                    has_lyrics_already = True
                audio_obj = FLAC(fp)
                duration = audio_obj.info.length
            elif fmt == "m4a":
                t = MP4(fp)
                if t.tags.get("\xa9ART"): artist = t.tags["\xa9ART"][0]
                if t.tags.get("\xa9nam"): clean_title = t.tags["\xa9nam"][0]
                if t.tags.get("\xa9lyr") and "[00:" in str(t.tags.get("\xa9lyr")[0]):
                    has_lyrics_already = True
                audio_obj = MP4(fp)
                duration = audio_obj.info.length
        except Exception:
            pass

        # Extraer artista principal y título limpio (sin colaboradores secundarios en la búsqueda)
        primary_artist = extract_primary_artist(artist)
        clean_track = extract_clean_track(clean_title, primary_artist)

        display_name = f"{primary_artist} - {clean_track}" if primary_artist else clean_track
        print(f"  [{idx}/{total}] 🎵 {BOLD}{display_name}{NC}")
        if artist and primary_artist and artist.strip() != primary_artist.strip():
            print(f"       {MAGENTA}👤 Colaboración detectada: \"{artist}\" → Artista principal: {primary_artist}{NC}")

        if has_lyrics_already:
            print(f"       {GREEN}✔ Ya contiene letras sincronizadas incrustadas. Omitiendo.{NC}")
            count_already += 1
            continue

        # Verificar si hay un .lrc local descargado por yt-dlp
        lyrics = None
        is_synced = False
        provider_name = "Local"
        lrc_file = fp.with_suffix('.lrc')
        if lrc_file.exists():
            try:
                with open(lrc_file, 'r', encoding='utf-8') as lf:
                    content = lf.read()
                    if "[00:" in content:
                        lyrics = content
                        is_synced = True
                        print(f"       {GREEN}✔ Archivo .lrc local detectado y sincronizado.{NC}")
            except Exception:
                pass

        # Si no hay letra local, consultar motores híbridos (LRCLIB + Multi-Web)
        if not lyrics:
            print(f"       {CYAN}🔍 Buscando letra ({primary_artist} - {clean_track})...{NC}")
            lyrics, is_synced, provider_name = fetch_lyrics_hybrid(primary_artist, clean_track, duration)
            time.sleep(0.35)

        if lyrics:
            if mode == "file":
                out_p = save_lyrics_to_file(fp, lyrics, is_synced)
                ltype = "sincronizada" if is_synced else "texto plano"
                print(f"       {GREEN}✔ Letra {ltype} [{provider_name}] guardada en {out_p.name}{NC}")
            else:
                if fmt == "mp3": embed_lyrics_mp3(fp, lyrics, is_synced)
                elif fmt == "flac": embed_lyrics_flac(fp, lyrics, is_synced)
                elif fmt == "m4a": embed_lyrics_m4a(fp, lyrics, is_synced)
                if is_synced:
                    print(f"       {GREEN}✔ Letra sincronizada [{provider_name}] incrustada y alineada con la pista de audio.{NC}")
                else:
                    print(f"       {YELLOW}✔ Letra en texto plano [{provider_name}] incrustada en la pista de audio.{NC}")
            count_processed += 1
        else:
            print(f"       {YELLOW}⚠ No se encontró letra disponible para esta pista de audio.{NC}")
            count_notfound += 1

    except Exception as e:
        print(f"       {RED}✘ Error procesando archivo: {e}{NC}")

print(f"\n  {BOLD}Resumen de Letras:{NC}")
print(f"  → {GREEN}Letras incrustadas/guardadas:{NC} {count_processed}")
print(f"  → {CYAN}Ya sincronizadas previamente:{NC} {count_already}")
print(f"  → {YELLOW}Sin letra encontrada:{NC}        {count_notfound}")
PYEOF
}

# ═══════════════════════════════════════════════════════════════
#  POST-PROCESO DE METADATOS
# ═══════════════════════════════════════════════════════════════
fix_metadata() {
    local dir="$1" fmt="$2" session_file="$3"
    python3 -c "import mutagen" 2>/dev/null || return 0

    python3 - "$dir" "$fmt" "$session_file" <<'PYEOF'
import sys, os, re
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
session_file = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else ""
PATTERN = re.compile(r'^(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+)$')
ext = {"mp3": ".mp3", "flac": ".flac", "m4a": ".m4a"}.get(fmt, f".{fmt}")

files_to_check = []
if session_file and os.path.isfile(session_file):
    with open(session_file, 'r', encoding='utf-8', errors='ignore') as sf:
        for line in sf:
            raw_p = line.strip()
            if raw_p:
                p = Path(raw_p)
                if p.is_file() and p.suffix.lower() == ext.lower() and p not in files_to_check:
                    files_to_check.append(p)
else:
    if target.is_file():
        files_to_check = [target] if target.suffix.lower() == ext.lower() else []
    elif target.is_dir():
        files_to_check = sorted([f for f in target.rglob("*") if f.is_file() and f.suffix.lower() == ext.lower()])

count = 0

for fp in files_to_check:
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
        echo "  ║                 🦦 OTTER RIP v6.1               ║"
        echo "  ╚══════════════════════════════════════════════════╝${NC}"
        echo -e "  ${BOLD}Perfil:${NC}      $PROFILE"
        echo -e "  ${BOLD}Duración:${NC}    >${DUR_MIN}s y ≤ $((DUR_MAX/60))m $((DUR_MAX%60))s"
        if [[ "$FETCH_LYRICS" == "true" ]]; then
            echo -e "  ${BOLD}Letras:${NC}      ${GREEN}✔ ON${NC} (${LYRICS_MODE}) [LRCLIB + Multi-Web]"
        else
            echo -e "  ${BOLD}Letras:${NC}      ${RED}✘ OFF${NC}"
        fi
        echo -e "  ${BOLD}Álbum:${NC}       $(album_layout_label)"
        echo -e "  ${BOLD}Portada:${NC}     ${COVER_LOCAL:-AUTO (thumbnail)}"
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar audio"
        echo -e "  ${CYAN}2)${NC} Configurar calidad"
        echo -e "  ${CYAN}3)${NC} Configurar límites de duración"
        echo -e "  ${CYAN}4)${NC} Configurar carpeta de álbum"
        echo -e "  ${CYAN}5)${NC} Configurar portada manual"
        echo -e "  ${CYAN}6)${NC} Configurar LETRAS (LRCLib & Multi-Web)"
        echo -e "  ${CYAN}7)${NC} Guía de calidad"
        echo -e "  ${CYAN}8)${NC} Abrir carpeta downloads"
        echo -e "  ${CYAN}0)${NC} Salir"
        echo ""
        read -rp "  Opción: " op
        case "$op" in
            1) menu_download ;;
            2) menu_quality ;;
            3) menu_duration ;;
            4) menu_album_layout ;;
            5) menu_cover ;;
            6) menu_lyrics ;;
            7) menu_guide ;;
            8) xdg-open "$DOWNLOADS" 2>/dev/null || thunar "$DOWNLOADS" 2>/dev/null || { echo -e "  ${CYAN}$DOWNLOADS${NC}"; sleep 1; } ;;
            0) echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"; exit 0 ;;
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
#  CONFIGURACIÓN DE LETRAS
# ═══════════════════════════════════════════════════════════════
menu_lyrics() {
    while true; do
        clear
        echo -e "${BOLD}${CYAN}══ Configuración de Letras (LRCLib) ══${NC}\n"
        echo -e "  OtterRip usa la API abierta de LRCLIB (no requiere API Key)."
        echo -e "  Puedes usar el servidor oficial en internet o tu servidor local Rust."
        echo ""
        echo -e "  ${BOLD}Estado:${NC}        ${FETCH_LYRICS:+${GREEN}ACTIVO${NC}}${FETCH_LYRICS:-${RED}INACTIVO${NC}}"
        echo -e "  ${BOLD}Modo:${NC}          ${LYRICS_MODE} ($([[ "$LYRICS_MODE" == "embed" ]] && echo "Incrustar en tags" || echo "Archivo .lrc/.txt separado"))"
        echo -e "  ${BOLD}Servidor API:${NC}  ${MAGENTA}${LRCLIB_HOST}${NC}"
        echo ""
        echo -e "  ${CYAN}1)${NC} Activar / Desactivar búsqueda de letras"
        echo -e "  ${CYAN}2)${NC} Modo: Incrustar en el archivo de audio (ID3/FLAC/M4A)"
        echo -e "  ${CYAN}3)${NC} Modo: Guardar como archivo .lrc / .txt separado"
        echo -e "  ${CYAN}4)${NC} Conectar a Servidor Oficial (https://lrclib.net)"
        echo -e "  ${CYAN}5)${NC} Conectar a Servidor Local (http://localhost:3300) [letras/]"
        echo -e "  ${CYAN}6)${NC} Ingresar URL de servidor personalizada"
        echo -e "  ${CYAN}0)${NC} Volver al menú principal"
        echo ""
        read -rp "  Opción: " l
        case "$l" in
            1) 
                if [[ "$FETCH_LYRICS" == "true" ]]; then
                    FETCH_LYRICS=false
                    echo -e "  ${YELLOW}Búsqueda de letras DESACTIVADA.${NC}"
                else
                    FETCH_LYRICS=true
                    echo -e "  ${GREEN}Búsqueda de letras ACTIVADA.${NC}"
                fi
                sleep 1 ;;
            2) 
                LYRICS_MODE="embed"
                echo -e "  ${GREEN}Modo: Incrustar en el archivo de audio.${NC}"
                sleep 1 ;;
            3) 
                LYRICS_MODE="file"
                echo -e "  ${GREEN}Modo: Crear archivo .lrc o .txt junto a la canción.${NC}"
                sleep 1 ;;
            4)
                LRCLIB_HOST="https://lrclib.net"
                echo -e "  ${GREEN}Servidor configurado a Oficial: $LRCLIB_HOST${NC}"
                sleep 1 ;;
            5)
                LRCLIB_HOST="http://localhost:3300"
                echo -e "  ${GREEN}Servidor configurado a Local: $LRCLIB_HOST${NC}"
                sleep 1 ;;
            6)
                read -rp "  Ingresa la URL del servidor API (ej. http://127.0.0.1:3300): " custom_host
                if [[ -n "$custom_host" ]]; then
                    LRCLIB_HOST="$custom_host"
                    echo -e "  ${GREEN}Servidor actualizado a: $LRCLIB_HOST${NC}"
                fi
                sleep 1 ;;
            0|"") return ;;
            *) echo -e "  ${RED}Opción inválida.${NC}"; sleep 1 ;;
        esac
    done
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
    echo -e "  ${YELLOW}ℹ${NC} Las letras se obtienen de lrclib.net (gratis, sin key)."
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
        local cur_display
        if [[ "$LAST_DOWNLOAD_FOLDER" == /* ]]; then
            cur_display="$LAST_DOWNLOAD_FOLDER"
        else
            cur_display="$DOWNLOADS/${LAST_DOWNLOAD_FOLDER:-Music}"
        fi
        echo -e "  ${BOLD}Ruta de guardado actual:${NC} ${CYAN}${cur_display}${NC}"
        read -rp "  ¿Guardar en ruta actual o escribir otra? (Enter=mantener actual): " folder_choice
        if [[ -n "$folder_choice" ]]; then
            if [[ "$folder_choice" == /* ]]; then
                LAST_DOWNLOAD_FOLDER="$folder_choice"
            else
                LAST_DOWNLOAD_FOLDER="$(safe_name "$folder_choice")"
            fi
        fi
        [[ -z "$LAST_DOWNLOAD_FOLDER" ]] && LAST_DOWNLOAD_FOLDER="Music"

        local DEST
        if [[ "$LAST_DOWNLOAD_FOLDER" == /* ]]; then
            DEST="$LAST_DOWNLOAD_FOLDER"
        else
            DEST="$DOWNLOADS/$LAST_DOWNLOAD_FOLDER"
        fi
        mkdir -p "$DEST"

        local ARCHIVE="$DEST/.otterrip_archive"

        # ── Templates de salida ─────────────────────────────────
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

        # ── Flags comunes ───────────────────────────────────────
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
        
        # NUEVO: Descargar letras si están disponibles en la fuente (LRC)
        if [[ "$FETCH_LYRICS" == "true" ]]; then
            COMMON_FLAGS+=( --write-sub --sub-lang "es,en" --sub-format "lrc/best" )
        fi

        [[ -n "$PP_VALUE" ]] && COMMON_FLAGS+=( --postprocessor-args "$PP_VALUE" )
        [[ "$AFMT" == "mp3" ]] && COMMON_FLAGS+=( --postprocessor-args "ffmpeg:-id3v2_version 3 -write_id3v1 1" )
        [[ -n "$COVER_LOCAL" && -f "$COVER_LOCAL" ]] \
            && COMMON_FLAGS+=( --ppa "EmbedThumbnail+ffmpeg_i1:${COVER_LOCAL}" )

        echo ""
        echo -e "${BOLD}${GREEN}  ══ Descargando ══${NC}"
        echo -e "  Perfil:   ${BOLD}$PROFILE${NC}"
        echo -e "  Duración: ${BOLD}>${DUR_MIN}s y ≤${DUR_MAX}s${NC}"
        echo -e "  Destino:  ${CYAN}$DEST${NC}"
        [[ "$FETCH_LYRICS" == "true" ]] && echo -e "  Letras:   ${MAGENTA}Activas [${LRCLIB_HOST}] (${LYRICS_MODE})${NC}"

        # ════════════════════════════════════════════════════════
        #  DESCARGA CON YT-DLP
        # ════════════════════════════════════════════════════════
        echo -e "  Modo:     ${CYAN}Descarga directa${NC}"
        [[ "$type_mode" == "album" ]] \
            && echo -e "  Álbum:    ${MAGENTA}$(album_layout_label)${NC}"
        echo -e "  ${YELLOW}Los elementos con error se omiten y yt-dlp continúa.${NC}"
        echo ""

        local SESSION_FILES
        SESSION_FILES="$(mktemp /tmp/otterrip_session_XXXXXX.txt 2>/dev/null || mktemp)"

        local -a CMD=(
            yt-dlp
            "${COMMON_FLAGS[@]}"
            "$PLFLAG"
            --print-to-file "after_move:filepath" "$SESSION_FILES"
            --progress
            --newline
            -o "$OUTTPL"
        )

        "${CMD[@]}" "$url"
        local status=$?

        # ── Post-proceso (Metadatos + Letras Web) ───────────────
        local new_count=0
        if [[ -f "$SESSION_FILES" ]]; then
            new_count=$(grep -c . "$SESSION_FILES" 2>/dev/null || echo 0)
        fi

        if (( new_count > 0 )); then
            echo ""
            echo -e "  ${YELLOW}⚙${NC}  Reparando metadatos de ${new_count} nueva(s) pista(s)..."
            fix_metadata "$DEST" "$AFMT" "$SESSION_FILES"
            
            if [[ "$FETCH_LYRICS" == "true" ]]; then
                echo -e "  ${YELLOW}⚙${NC}  Buscando e incrustando letras de las pistas descargadas (${new_count} en cola)..."
                process_lyrics "$DEST" "$AFMT" "$SESSION_FILES"
            fi
        else
            echo ""
            echo -e "  ${GREEN}✔ No hay pistas nuevas que requieran procesamiento (ya descargadas).${NC}"
        fi

        rm -f "$SESSION_FILES"

        echo ""
        if (( status == 0 )); then
            echo -e "${GREEN}${BOLD}  ✔ Proceso finalizado correctamente.${NC}"
        else
            echo -e "${YELLOW}${BOLD}  ⚠ Proceso terminado con avisos/errores parciales.${NC}"
        fi
        echo -e "  Archivos en: ${CYAN}$DEST${NC}"
        echo ""
        echo -e "  ${CYAN}1)${NC} Descargar más (misma ruta: ${BOLD}${LAST_DOWNLOAD_FOLDER}${NC})"
        echo -e "  ${CYAN}2)${NC} Descargar más en OTRA ruta"
        echo -e "  ${CYAN}3)${NC} Volver al menú principal"
        echo -e "  ${CYAN}4)${NC} Salir"
        echo ""
        read -rp "  Opción (Enter=1): " post_op
        [[ -z "$post_op" ]] && post_op="1"
        case "$post_op" in
            1) 
                seguir=true 
                ;;
            2) 
                seguir=true
                echo ""
                local cur_p
                if [[ "$LAST_DOWNLOAD_FOLDER" == /* ]]; then
                    cur_p="$LAST_DOWNLOAD_FOLDER"
                else
                    cur_p="$DOWNLOADS/$LAST_DOWNLOAD_FOLDER"
                fi
                echo -e "  ${BOLD}Ruta actual:${NC} ${CYAN}$cur_p${NC}"
                read -rp "  Ingresa la nueva carpeta o ruta (Enter=mantener actual): " new_dest
                if [[ -n "$new_dest" ]]; then
                    if [[ "$new_dest" == /* ]]; then
                        LAST_DOWNLOAD_FOLDER="$new_dest"
                    else
                        LAST_DOWNLOAD_FOLDER="$(safe_name "$new_dest")"
                    fi
                fi
                ;;
            4) 
                echo -e "\n  ${GREEN}¡Hasta luego!${NC}\n"
                exit 0 
                ;;
            *) 
                seguir=false 
                ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  INICIO
# ═══════════════════════════════════════════════════════════════
install_deps
menu

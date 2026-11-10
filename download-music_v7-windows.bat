@echo off
setlocal
title OtterRip v6.1
REM ==============================================================
REM  OtterRip v6.1 - archivo .bat de un solo fichero
REM  Este .bat contiene el programa (PowerShell) incrustado debajo
REM  de la linea marcadora. Al ejecutarlo se extrae a un .ps1
REM  temporal y se lanza con -STA (necesario para el selector de
REM  carpetas). Basta con hacer doble clic.
REM  NO borres ni cambies la linea  #__OTTERRIP_PS1__
REM ==============================================================
set "OTTERRIP_HOME=%~dp0"
set "OTTERRIP_SELF=%~f0"
set "OTTERRIP_PS1=%TEMP%\otterrip_%RANDOM%%RANDOM%.ps1"

powershell -NoProfile -ExecutionPolicy Bypass -STA -Command "$l=[IO.File]::ReadAllLines($env:OTTERRIP_SELF,[Text.Encoding]::UTF8); $n=[Array]::IndexOf($l,'#__OTTERRIP_PS1__'); if($n -lt 0){Write-Host 'No se encontro el bloque PowerShell.'; exit 9}; [IO.File]::WriteAllLines($env:OTTERRIP_PS1,$l[($n+1)..($l.Length-1)],(New-Object Text.UTF8Encoding($true))); & $env:OTTERRIP_PS1; exit $LASTEXITCODE"
set "RC=%ERRORLEVEL%"
del "%OTTERRIP_PS1%" >nul 2>&1
if not "%RC%"=="0" pause
exit /b %RC%
#__OTTERRIP_PS1__
# ═══════════════════════════════════════════════════════════════
#  OtterRip v6.1 — Descargador de audio + Letras (LRCLib API) · Windows
#  + Descargas paralelas (job pool)
#  + Selector de carpeta de destino (ventana de Windows)
#  + Búsqueda e incrustación de letras vía lrclib.net (+ metabuscador)
#  (versión .bat: este bloque se ejecuta desde OtterRip.bat)
# ═══════════════════════════════════════════════════════════════

# El selector de carpetas necesita un hilo STA; si no lo es, se relanza.
if ($PSCommandPath -and [System.Threading.Thread]::CurrentThread.GetApartmentState() -ne [System.Threading.ApartmentState]::STA) {
    $self = (Get-Process -Id $PID).Path
    & $self -NoProfile -ExecutionPolicy Bypass -STA -File $PSCommandPath
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

# Carpeta base = donde está el .bat (el .bat la pasa en OTTERRIP_HOME)
$BASE_DIR = if ($env:OTTERRIP_HOME) { $env:OTTERRIP_HOME } elseif ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$BIN_DIR  = Join-Path $BASE_DIR 'bin'
if (Test-Path -LiteralPath $BIN_DIR) { $env:Path = "$BIN_DIR;$env:Path" }

# ── Estado global ────────────────────────────────────────────────
$AFMT          = 'mp3'
$AQ            = '0'
$PP_VALUE      = ''
$PROFILE_NAME  = 'MP3 320 kbps'
$COVER_LOCAL   = ''
$DUR_MIN       = 60
$DUR_MAX       = 480
$ALBUM_LAYOUT  = 'single'
$PARALLEL_JOBS = 3          # workers concurrentes (1 = secuencial)
$LAST_DIR      = ''         # última carpeta elegida en el selector
$YTDLP         = $null
$PY_EXE        = $null
$PY_ARGS       = @()
$HAS_MUTAGEN   = $false
$FETCH_LYRICS  = $true      # Activar/desactivar búsqueda de letras
$LYRICS_MODE   = 'embed'    # 'embed' (en el archivo) o 'file' (.lrc aparte)
$LRCLIB_HOST   = 'https://lrclib.net'   # oficial o local (http://localhost:3300)
$HAS_LYRICS    = $false     # Python + mutagen + requests disponibles
$HAS_SYNCED    = $false     # syncedlyrics (metabuscador multired) disponible
$DET_TYPE      = 'song'
$DET_TITLE     = ''
$DET_COUNT     = ''

# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

# Texto con colores en línea: {r} {g} {y} {c} {m} {b} y {/} para cerrar
function Say {
    param([string]$Text = '', [switch]$NoNewLine)
    $colors = @{ r = 'Red'; g = 'Green'; y = 'Yellow'; c = 'Cyan'; m = 'Magenta'; b = 'White' }
    $color = $null
    foreach ($part in [regex]::Split($Text, '(\{[rgycmb/]\})')) {
        if ($part -match '^\{([rgycmb/])\}$') {
            $t = $Matches[1]
            $color = if ($t -eq '/') { $null } else { $colors[$t] }
        } elseif ($part.Length -gt 0) {
            if ($color) { Write-Host $part -NoNewline -ForegroundColor $color }
            else        { Write-Host $part -NoNewline }
        }
    }
    if (-not $NoNewLine) { Write-Host '' }
}

function Ask([string]$Prompt) { return ("$(Read-Host $Prompt)").Trim() }

function Test-Cmd([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-PositiveInt($v) {
    $n = [int64]0
    return (("$v" -match '^[0-9]+$') -and [int64]::TryParse("$v", [ref]$n) -and ($n -gt 0))
}

# Nombre seguro para carpetas/archivos de Windows
function ConvertTo-SafeName([string]$s) {
    $s = $s -replace '[<>:"/\\|?*\x00-\x1f]', '_'
    return $s.Trim().TrimEnd('.', ' ')
}

# Cita un argumento para la línea de comandos de Windows
function ConvertTo-QuotedArg([string]$a) {
    if ($a -eq '') { return '""' }
    if ($a -notmatch '[\s"]') { return $a }
    $a = $a -replace '(\\*)"', '$1$1\"'
    $a = $a -replace '(\\+)$', '$1$1'
    return '"' + $a + '"'
}

function Update-SessionPath {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ';'
    if (Test-Path -LiteralPath $BIN_DIR) { $env:Path = "$BIN_DIR;$env:Path" }
}

function Resolve-Tools {
    $c = Get-Command yt-dlp -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $script:YTDLP = if ($c) { $c.Source } else { $null }
}

# Instala con winget → scoop → choco (el primero que exista y funcione)
function Install-Tool {
    param([string]$Cmd, [string]$WingetId, [string]$ScoopName, [string]$ChocoName)
    if ($WingetId -and (Test-Cmd winget)) {
        & winget install -e --id $WingetId --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        Update-SessionPath
        if (Test-Cmd $Cmd) { return $true }
    }
    if ($ScoopName -and (Test-Cmd scoop)) {
        & scoop install $ScoopName 2>&1 | Out-Null
        Update-SessionPath
        if (Test-Cmd $Cmd) { return $true }
    }
    if ($ChocoName -and (Test-Cmd choco)) {
        & choco install $ChocoName -y 2>&1 | Out-Null
        Update-SessionPath
        if (Test-Cmd $Cmd) { return $true }
    }
    return $false
}

# Último recurso: bajar yt-dlp.exe junto al script (carpeta bin)
function Install-YtDlpDirect {
    try {
        New-Item -ItemType Directory -Force -Path $BIN_DIR | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' `
            -OutFile (Join-Path $BIN_DIR 'yt-dlp.exe')
        Update-SessionPath
        return (Test-Cmd yt-dlp)
    } catch {
        return $false
    }
}

# Busca un Python 3 que funcione (ignora el stub de la Microsoft Store)
function Find-Python {
    foreach ($cand in @('py -3', 'python', 'python3')) {
        $parts = $cand -split ' '
        $exe   = $parts[0]
        $extra = @($parts | Select-Object -Skip 1)
        if (-not (Test-Cmd $exe)) { continue }
        try { $v = & $exe @extra -c "import sys; print(sys.version_info[0])" 2>$null } catch { $v = $null }
        if ("$v".Trim() -eq '3') {
            $script:PY_EXE  = $exe
            $script:PY_ARGS = $extra
            return $true
        }
    }
    return $false
}

function Test-Mutagen {
    if (-not $PY_EXE) { return $false }
    & $PY_EXE @PY_ARGS -c "import mutagen" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# Comprueba que Python pueda ejecutar una sentencia (p. ej. 'import requests')
function Test-PyImport([string]$Stmt) {
    if (-not $PY_EXE) { return $false }
    & $PY_EXE @PY_ARGS -c $Stmt 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# Activa los colores ANSI de la consola (la salida de Python los usa)
function Enable-VT {
    try {
        if (-not ('OtterRip.ConsoleVT' -as [type])) {
            $sig = '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n); ' +
                   '[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m); ' +
                   '[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);'
            Add-Type -Namespace OtterRip -Name ConsoleVT -MemberDefinition $sig
        }
        $h = [OtterRip.ConsoleVT]::GetStdHandle(-11)
        $mode = [uint32]0
        if (-not [OtterRip.ConsoleVT]::GetConsoleMode($h, [ref]$mode)) { return $false }
        return [OtterRip.ConsoleVT]::SetConsoleMode($h, [uint32]($mode -bor 4))
    } catch {
        return $false
    }
}

# ═══════════════════════════════════════════════════════════════
#  DEPENDENCIAS
# ═══════════════════════════════════════════════════════════════
function Install-Deps {
    Say "`n{c}══ Verificando dependencias ══{/}`n"

    # ── ffmpeg ──
    if (-not (Test-Cmd ffmpeg)) {
        Say "  {y}⚙{/} Instalando ffmpeg..."
        if (-not (Install-Tool -Cmd 'ffmpeg' -WingetId 'Gyan.FFmpeg' -ScoopName 'ffmpeg' -ChocoName 'ffmpeg')) {
            Say "  {r}✘ No se pudo instalar ffmpeg. Instálalo a mano:  winget install Gyan.FFmpeg{/}"
            Read-Host "  Enter para salir" | Out-Null
            exit 1
        }
        Say "  {g}✔{/} ffmpeg instalado"
    } else {
        $v = (& ffmpeg -version 2>&1 | Select-Object -First 1) -split '\s+'
        Say "  {g}✔{/} ffmpeg $($v[2])"
    }

    # ── yt-dlp ──
    if (-not (Test-Cmd yt-dlp)) {
        Say "  {y}⚙{/} Instalando yt-dlp..."
        $okInstall = Install-Tool -Cmd 'yt-dlp' -WingetId 'yt-dlp.yt-dlp' -ScoopName 'yt-dlp' -ChocoName 'yt-dlp'
        if (-not $okInstall) {
            Say "  {y}⚙{/} Descargando yt-dlp.exe desde GitHub..."
            $okInstall = Install-YtDlpDirect
        }
        if (-not $okInstall) {
            Say "  {r}✘ No se pudo instalar yt-dlp. Instálalo a mano:  winget install yt-dlp.yt-dlp{/}"
            Read-Host "  Enter para salir" | Out-Null
            exit 1
        }
        Say "  {g}✔{/} yt-dlp instalado"
    } else {
        Say "  {g}✔{/} yt-dlp $(& yt-dlp --version 2>$null)"
        Say "  {y}⟳{/}  Actualizando yt-dlp..."
        try { & yt-dlp -U --quiet 2>$null | Out-Null } catch {}
    }
    Resolve-Tools

    # ── Python + mutagen (reparar metadatos / portada manual / portadas en M4A) ──
    if (-not (Find-Python)) {
        Say "  {y}⚠{/} Python no encontrado (se usa para reparar metadatos y portada manual)."
        if (Test-Cmd winget) {
            $r = Ask "  ¿Instalar Python con winget? [s/N]"
            if ($r -match '^[sSyY]') {
                Say "  {y}⚙{/} Instalando Python..."
                & winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
                Update-SessionPath
                [void](Find-Python)
            }
        }
    }
    $script:HAS_MUTAGEN = $false
    if ($PY_EXE) {
        if (Test-Mutagen) {
            Say "  {g}✔{/} python-mutagen ya instalado"
            $script:HAS_MUTAGEN = $true
        } else {
            Say "  {y}⚙{/} Instalando mutagen..."
            & $PY_EXE @PY_ARGS -m pip install --user -q mutagen 2>&1 | Out-Null
            if (Test-Mutagen) {
                Say "  {g}✔{/} mutagen instalado"
                $script:HAS_MUTAGEN = $true
            } else {
                Say "  {y}⚠{/} mutagen no disponible"
            }
        }
    }
    if (-not $HAS_MUTAGEN) {
        Say "  {y}⚠{/} Sin mutagen: se omite la reparación de metadatos, la portada manual y las letras."
    }

    # ── Letras: requests (LRCLIB) + syncedlyrics (metabuscador multired, opcional) ──
    $script:HAS_LYRICS = $false
    $script:HAS_SYNCED = $false
    if ($HAS_MUTAGEN) {
        if (-not (Test-PyImport 'import requests')) {
            Say "  {y}⚙{/} Instalando requests..."
            & $PY_EXE @PY_ARGS -m pip install --user -q requests 2>&1 | Out-Null
        }
        if (Test-PyImport 'import requests') {
            Say "  {g}✔{/} python-requests (letras LRCLIB)"
            $script:HAS_LYRICS = $true
        } else {
            Say "  {y}⚠{/} requests no disponible: se omite la búsqueda de letras."
        }
        if ($HAS_LYRICS) {
            if (-not (Test-PyImport 'import syncedlyrics')) {
                Say "  {y}⚙{/} Instalando syncedlyrics (metabuscador de letras)..."
                & $PY_EXE @PY_ARGS -m pip install --user -q syncedlyrics 2>&1 | Out-Null
            }
            if (Test-PyImport 'import syncedlyrics') {
                Say "  {g}✔{/} syncedlyrics (Musixmatch / NetEase / Megalobiz)"
                $script:HAS_SYNCED = $true
            } else {
                Say "  {y}⚠{/} syncedlyrics no disponible: las letras usarán solo LRCLIB."
            }
        }
    }

    Say "`n{g}  Dependencias listas.{/}`n"
    Start-Sleep -Seconds 1
}

# ═══════════════════════════════════════════════════════════════
#  POST-PROCESO DE METADATOS
#  Solo toca archivos creados/modificados durante esta descarga
#  (la carpeta elegida puede ser una biblioteca ya existente).
# ═══════════════════════════════════════════════════════════════
$PY_FIXMETA = @'
import sys, re
from pathlib import Path

try:
    from mutagen.mp3  import MP3
    from mutagen.id3  import ID3, TIT2, TPE1, APIC, ID3NoHeaderError
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4  import MP4, MP4Cover
except ImportError:
    sys.exit(0)

target = Path(sys.argv[1])
fmt    = sys.argv[2]
since  = float(sys.argv[3])
cover  = Path(sys.argv[4]) if len(sys.argv) > 4 else None

cover_data = None
cover_mime = "image/jpeg"
if cover and cover.is_file():
    cover_data = cover.read_bytes()
    if cover.suffix.lower() == ".png":
        cover_mime = "image/png"

PATTERN = re.compile(r'^(?P<artist>.+?)\s*[-–—]\s*(?P<title>.+)$')
ext = {"mp3": ".mp3", "flac": ".flac", "m4a": ".m4a"}.get(fmt, "." + fmt)
count = 0

for fp in sorted(target.rglob("*" + ext)):
    try:
        if fp.stat().st_mtime < since:
            continue

        if fmt == "mp3":
            MP3(fp)
            try:
                tags = ID3(fp)
            except ID3NoHeaderError:
                tags = ID3()
            artist_tag = tags.get("TPE1")
            title_tag  = tags.get("TIT2")
            if (not artist_tag or not str(artist_tag)) and title_tag:
                m = PATTERN.match(str(title_tag))
                if m:
                    tags.setall("TPE1", [TPE1(encoding=3, text=m.group("artist"))])
                    tags.setall("TIT2", [TIT2(encoding=3, text=m.group("title"))])
            if cover_data:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover_data))
            tags.save(str(fp), v2_version=3, v1=2)

        elif fmt == "flac":
            audio  = FLAC(fp)
            artist = audio.get("artist", [""])[0]
            title  = audio.get("title", [""])[0]
            if not artist and title:
                m = PATTERN.match(title)
                if m:
                    audio["artist"] = [m.group("artist")]
                    audio["title"]  = [m.group("title")]
            if cover_data:
                pic = Picture()
                pic.type = 3
                pic.mime = cover_mime
                pic.desc = "Cover"
                pic.data = cover_data
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()

        elif fmt == "m4a":
            audio = MP4(fp)
            if audio.tags is None:
                audio.add_tags()
            artist = (audio.tags.get("\xa9ART") or [""])[0]
            title  = (audio.tags.get("\xa9nam") or [""])[0]
            if not artist and title:
                m = PATTERN.match(title)
                if m:
                    audio.tags["\xa9ART"] = [m.group("artist")]
                    audio.tags["\xa9nam"] = [m.group("title")]
            if cover_data:
                fmt_c = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
                audio.tags["covr"] = [MP4Cover(cover_data, imageformat=fmt_c)]
            audio.save()

        count += 1
    except Exception:
        pass

if count:
    print("  -> Metadatos verificados en %d archivo(s)." % count)
'@

function Repair-Metadata {
    param([string]$Dir, [string]$Fmt, [long]$Since)
    if (-not $HAS_MUTAGEN -or -not $PY_EXE) { return }

    $pyFile = Join-Path $env:TEMP 'otterrip_fixmeta.py'
    [System.IO.File]::WriteAllText($pyFile, $PY_FIXMETA, (New-Object System.Text.UTF8Encoding($false)))

    # Sin barra final (evita que \" escape la comilla de cierre)
    $dirArg = if ($Dir.Length -gt 3) { $Dir.TrimEnd('\') } else { $Dir + '.' }

    $pyArgs = @($PY_ARGS) + @($pyFile, $dirArg, $Fmt, "$Since")
    if ($COVER_LOCAL -and (Test-Path -LiteralPath $COVER_LOCAL)) { $pyArgs += $COVER_LOCAL }
    & $PY_EXE @pyArgs
}

# ═══════════════════════════════════════════════════════════════
#  LETRAS (LRCLIB + metabuscador)
#  El script Python se escribe en %TEMP% y se ejecuta al final de cada
#  descarga, solo sobre las pistas nuevas (lista que escribe yt-dlp).
# ═══════════════════════════════════════════════════════════════
$PY_LYRICS = @'
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

# ANSI Colors para feedback visual en terminal (OTTERRIP_NOCOLOR=1 los desactiva)
if os.environ.get('OTTERRIP_NOCOLOR'):
    GREEN = YELLOW = CYAN = MAGENTA = RED = BOLD = NC = ''
else:
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
'@

function Invoke-Lyrics {
    param([string]$Dir, [string]$Fmt, [string]$SessionFile)
    if (-not $FETCH_LYRICS) { return }
    if (-not $HAS_LYRICS -or -not $PY_EXE) {
        Say "  {y}⚠ Letras omitidas: requiere Python + mutagen + requests.{/}"
        return
    }

    $pyFile = Join-Path $env:TEMP 'otterrip_lyrics.py'
    [System.IO.File]::WriteAllText($pyFile, $PY_LYRICS, (New-Object System.Text.UTF8Encoding($false)))

    # Metabuscador local opcional (carpeta junto al .bat); si no existe se usa el instalado con pip
    $syncedDir = Join-Path $BASE_DIR 'letras-webs-buscar'

    # Sin barra final (evita que una barra invertida escape la comilla de cierre)
    $dirArg = if ($Dir.Length -gt 3) { $Dir.TrimEnd([char]92) } else { $Dir + '.' }

    # Colores ANSI en la salida de Python; si la consola no los admite se desactivan
    $env:OTTERRIP_NOCOLOR = $(if (Enable-VT) { '' } else { '1' })

    & $PY_EXE @PY_ARGS $pyFile $dirArg $Fmt $LYRICS_MODE $LRCLIB_HOST $syncedDir $SessionFile
}

# ═══════════════════════════════════════════════════════════════
#  MENÚ PRINCIPAL
# ═══════════════════════════════════════════════════════════════
function Get-AlbumLayoutLabel {
    if ($ALBUM_LAYOUT -eq 'single') { return 'Todos los temas en una sola carpeta' }
    return 'Una carpeta por álbum'
}

function Show-MainMenu {
    while ($true) {
        Clear-Host
        Say "{c}  ╔══════════════════════════════════════════════════╗{/}"
        Say "{c}  ║                🦦 OTTER RIP v6.1                ║{/}"
        Say "{c}  ╚══════════════════════════════════════════════════╝{/}"
        $durTxt = ">$($DUR_MIN)s y ≤ $([math]::Floor($DUR_MAX / 60))m $($DUR_MAX % 60)s"
        $parTxt = if ($PARALLEL_JOBS -gt 1) { '(activo)' } else { '(secuencial)' }
        $coverTxt = if ($COVER_LOCAL) { $COVER_LOCAL } else { 'AUTO (thumbnail)' }
        $destTxt  = if ($LAST_DIR) { "se elige al descargar (última: $LAST_DIR)" } else { 'se elige al descargar (ventana de Windows)' }
        Say "  {b}Perfil:{/}      $PROFILE_NAME"
        Say "  {b}Duración:{/}    $durTxt"
        Say "  {b}Paralelo:{/}    $PARALLEL_JOBS worker(s) $parTxt"
        $lyEng = if ($HAS_SYNCED) { 'LRCLIB + Multi-Web' } else { 'LRCLIB' }
        $lyTxt = if ($FETCH_LYRICS) { "{g}✔ ON{/} ($LYRICS_MODE) [$lyEng]" } else { '{r}✘ OFF{/}' }
        Say "  {b}Letras:{/}      $lyTxt"
        Say "  {b}Álbum:{/}       $(Get-AlbumLayoutLabel)"
        Say "  {b}Portada:{/}     $coverTxt"
        Say "  {b}Destino:{/}     $destTxt"
        Say ""
        Say "  {c}1){/} Descargar audio"
        Say "  {c}2){/} Configurar calidad"
        Say "  {c}3){/} Configurar límites de duración"
        Say "  {c}4){/} Configurar descargas paralelas"
        Say "  {c}5){/} Configurar carpeta de álbum"
        Say "  {c}6){/} Configurar portada manual"
        Say "  {c}7){/} Configurar LETRAS (LRCLib & Multi-Web)"
        Say "  {c}8){/} Guía de calidad"
        Say "  {c}9){/} Abrir última carpeta de destino"
        Say "  {c}0){/} Salir"
        Say ""
        $op = Ask "  Opción"
        switch ($op) {
            '1' { Invoke-DownloadMenu }
            '2' { Show-QualityMenu }
            '3' { Show-DurationMenu }
            '4' { Show-ParallelMenu }
            '5' { Show-AlbumLayoutMenu }
            '6' { Show-CoverMenu }
            '7' { Show-LyricsMenu }
            '8' { Show-GuideMenu }
            '9' {
                $p = if ($LAST_DIR -and (Test-Path -LiteralPath $LAST_DIR)) { $LAST_DIR } else { [Environment]::GetFolderPath('MyMusic') }
                Invoke-Item -LiteralPath $p
            }
            '0' { Say "`n  {g}¡Hasta luego!{/}`n"; exit 0 }
            default { Say "  {r}Opción inválida.{/}"; Start-Sleep -Seconds 1 }
        }
    }
}

# ═══════════════════════════════════════════════════════════════
#  CALIDAD
# ═══════════════════════════════════════════════════════════════
function Show-QualityMenu {
    Clear-Host
    Say "{c}══ Calidad de audio ══{/}`n"
    Say "  {c}1){/} MP3  320 kbps"
    Say "  {c}2){/} M4A  AAC 256 kbps"
    Say "  {c}3){/} M4A  AAC 320 kbps"
    Say "  {c}4){/} FLAC 16-bit / 44.1 kHz"
    Say "  {c}5){/} FLAC 24-bit / 96 kHz"
    Say ""
    $q = Ask "  Opción"
    switch ($q) {
        '1' { $script:AFMT = 'mp3';  $script:AQ = '0'; $script:PP_VALUE = '';                                          $script:PROFILE_NAME = 'MP3 320 kbps' }
        '2' { $script:AFMT = 'm4a';  $script:AQ = '0'; $script:PP_VALUE = 'ffmpeg:-c:a aac -b:a 256k';                  $script:PROFILE_NAME = 'M4A AAC 256 kbps' }
        '3' { $script:AFMT = 'm4a';  $script:AQ = '0'; $script:PP_VALUE = 'ffmpeg:-c:a aac -b:a 320k';                  $script:PROFILE_NAME = 'M4A AAC 320 kbps' }
        '4' { $script:AFMT = 'flac'; $script:AQ = '0'; $script:PP_VALUE = 'ffmpeg:-sample_fmt s16 -ar 44100';           $script:PROFILE_NAME = 'FLAC 16-bit/44.1kHz' }
        '5' { $script:AFMT = 'flac'; $script:AQ = '0'; $script:PP_VALUE = 'ffmpeg:-sample_fmt s32 -ar 96000';           $script:PROFILE_NAME = 'FLAC 24-bit/96kHz' }
        default { Say "  {r}Inválido.{/}"; Start-Sleep -Seconds 1 }
    }
}

# ═══════════════════════════════════════════════════════════════
#  DURACIÓN
# ═══════════════════════════════════════════════════════════════
function Show-DurationMenu {
    Clear-Host
    Say "{c}══ Límites de duración ══{/}`n"
    Say "  Solo se descargan audios que cumplan:"
    Say "  {g}duración > mínima{/}  y  {g}duración ≤ máxima{/}"
    Say ""
    Say "  Actual → mín: {b}$($DUR_MIN)s{/}   máx: {b}$($DUR_MAX)s{/}"
    Say ""
    $dmin = Ask "  Duración mínima en segundos (Enter=$DUR_MIN)"
    $dmax = Ask "  Duración máxima en segundos (Enter=$DUR_MAX)"
    if (-not $dmin) { $dmin = "$DUR_MIN" }
    if (-not $dmax) { $dmax = "$DUR_MAX" }
    if (-not (Test-PositiveInt $dmin) -or -not (Test-PositiveInt $dmax) -or ([int64]$dmin -ge [int64]$dmax)) {
        Say "  {r}✘ Rango inválido.{/}"; Start-Sleep -Seconds 2; return
    }
    $script:DUR_MIN = [int]$dmin
    $script:DUR_MAX = [int]$dmax
    Say "`n  {g}✔ Rango: >$($DUR_MIN)s y ≤$($DUR_MAX)s{/}"
    Start-Sleep -Seconds 1
}

# ═══════════════════════════════════════════════════════════════
#  DESCARGAS PARALELAS
# ═══════════════════════════════════════════════════════════════
function Show-ParallelMenu {
    Clear-Host
    Say "{c}══ Descargas paralelas ══{/}`n"
    Say "  Cuántas canciones se descargan {b}al mismo tiempo{/} en playlists/álbumes."
    Say "  Workers actuales: {b}$PARALLEL_JOBS{/}"
    Say ""
    Say "  {c}Recomendaciones:{/}"
    Say "   1 → Secuencial (sin paralelismo, comportamiento clásico)"
    Say "   3 → Equilibrado  {g}← recomendado{/}"
    Say "   5 → Rápido (más CPU/RAM/ancho de banda)"
    Say "   8 → Máximo (puede fallar si la fuente limita conexiones)"
    Say ""
    $n = Ask "  Número de workers [1-8] (Enter=$PARALLEL_JOBS)"
    if (-not $n) {
        Say "  {g}Sin cambios.{/}"
    } elseif ($n -match '^[1-8]$') {
        $script:PARALLEL_JOBS = [int]$n
        Say "  {g}✔ Workers: $PARALLEL_JOBS{/}"
    } else {
        Say "  {r}Inválido. Elige entre 1 y 8.{/}"
    }
    Start-Sleep -Seconds 1
}

# ═══════════════════════════════════════════════════════════════
#  ÁLBUM LAYOUT
# ═══════════════════════════════════════════════════════════════
function Show-AlbumLayoutMenu {
    Clear-Host
    Say "{c}══ Organización de álbumes ══{/}`n"
    Say "  {c}1){/} Todos los temas del álbum en UNA sola carpeta"
    Say "  {c}2){/} UNA carpeta separada por álbum"
    Say ""
    $a = Ask "  Opción [1/2] (Enter=1)"
    if (-not $a) { $a = '1' }
    switch ($a) {
        '1' { $script:ALBUM_LAYOUT = 'single' }
        '2' { $script:ALBUM_LAYOUT = 'split' }
        default { Say "  {r}Inválido.{/}"; Start-Sleep -Seconds 1; return }
    }
    Say "  {g}✔ $(Get-AlbumLayoutLabel){/}"
    Start-Sleep -Seconds 1
}

# ═══════════════════════════════════════════════════════════════
#  PORTADA
# ═══════════════════════════════════════════════════════════════
function Show-CoverMenu {
    Clear-Host
    Say "{c}══ Portada manual ══{/}`n"
    Say "  Deja vacío para usar thumbnail automático."
    Say "  (Puedes arrastrar el archivo a esta ventana para pegar su ruta.)"
    Say ""
    $raw = (Ask "  Ruta JPG/PNG").Trim('"')
    if (-not $raw) {
        $script:COVER_LOCAL = ''
        Say "  {g}Portada: AUTO{/}"
    } elseif (Test-Path -LiteralPath $raw -PathType Leaf) {
        $script:COVER_LOCAL = (Resolve-Path -LiteralPath $raw).Path
        Say "  {g}✔ $COVER_LOCAL{/}"
        if (-not $HAS_MUTAGEN) { Say "  {y}⚠ Requiere Python + mutagen; sin ellos no se aplicará.{/}" }
    } else {
        Say "  {r}Archivo no encontrado. Sin cambios.{/}"
    }
    Start-Sleep -Seconds 1
}

# ═══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LETRAS
# ═══════════════════════════════════════════════════════════════
function Show-LyricsMenu {
    while ($true) {
        Clear-Host
        Say "{c}══ Configuración de Letras (LRCLib) ══{/}`n"
        Say "  OtterRip usa la API abierta de LRCLIB (no requiere API Key)."
        Say "  Puedes usar el servidor oficial en internet o tu servidor local Rust."
        Say ""
        $st = if ($FETCH_LYRICS) { '{g}ACTIVO{/}' } else { '{r}INACTIVO{/}' }
        $md = if ($LYRICS_MODE -eq 'embed') { 'Incrustar en tags' } else { 'Archivo .lrc/.txt separado' }
        $en = if ($HAS_SYNCED) { 'LRCLIB + Multi-Web (Musixmatch, NetEase, Megalobiz)' } else { 'solo LRCLIB (syncedlyrics no instalado)' }
        Say "  {b}Estado:{/}        $st"
        Say "  {b}Modo:{/}          $LYRICS_MODE ($md)"
        Say "  {b}Servidor API:{/}  {m}$LRCLIB_HOST{/}"
        Say "  {b}Motores:{/}       $en"
        if (-not $HAS_LYRICS) { Say "  {y}⚠ Requiere Python + mutagen + requests (no disponibles ahora).{/}" }
        Say ""
        Say "  {c}1){/} Activar / Desactivar búsqueda de letras"
        Say "  {c}2){/} Modo: Incrustar en el archivo de audio (ID3/FLAC/M4A)"
        Say "  {c}3){/} Modo: Guardar como archivo .lrc / .txt separado"
        Say "  {c}4){/} Conectar a Servidor Oficial (https://lrclib.net)"
        Say "  {c}5){/} Conectar a Servidor Local (http://localhost:3300) [letras/]"
        Say "  {c}6){/} Ingresar URL de servidor personalizada"
        Say "  {c}0){/} Volver al menú principal"
        Say ""
        $l = Ask "  Opción"
        switch ($l) {
            '1' {
                $script:FETCH_LYRICS = -not $FETCH_LYRICS
                if ($FETCH_LYRICS) { Say "  {g}Búsqueda de letras ACTIVADA.{/}" }
                else               { Say "  {y}Búsqueda de letras DESACTIVADA.{/}" }
                Start-Sleep -Seconds 1
            }
            '2' {
                $script:LYRICS_MODE = 'embed'
                Say "  {g}Modo: Incrustar en el archivo de audio.{/}"
                Start-Sleep -Seconds 1
            }
            '3' {
                $script:LYRICS_MODE = 'file'
                Say "  {g}Modo: Crear archivo .lrc o .txt junto a la canción.{/}"
                Start-Sleep -Seconds 1
            }
            '4' {
                $script:LRCLIB_HOST = 'https://lrclib.net'
                Say "  {g}Servidor configurado a Oficial: $LRCLIB_HOST{/}"
                Start-Sleep -Seconds 1
            }
            '5' {
                $script:LRCLIB_HOST = 'http://localhost:3300'
                Say "  {g}Servidor configurado a Local: $LRCLIB_HOST{/}"
                Start-Sleep -Seconds 1
            }
            '6' {
                $custom = Ask "  Ingresa la URL del servidor API (ej. http://127.0.0.1:3300)"
                if ($custom -match '^https?://') {
                    $script:LRCLIB_HOST = $custom.TrimEnd('/')
                    Say "  {g}Servidor actualizado a: $LRCLIB_HOST{/}"
                } elseif ($custom) {
                    Say "  {r}La URL debe empezar por http:// o https://{/}"
                }
                Start-Sleep -Seconds 1
            }
            { $_ -eq '0' -or $_ -eq '' } { return }
            default { Say "  {r}Opción inválida.{/}"; Start-Sleep -Seconds 1 }
        }
    }
}

# ═══════════════════════════════════════════════════════════════
#  GUÍA
# ═══════════════════════════════════════════════════════════════
function Show-GuideMenu {
    Clear-Host
    Say "{c}══ Guía de calidad ══{/}`n"
    Say "  {b}MP3 320 kbps{/}       — Muy compatible."
    Say "  {b}M4A AAC 256/320{/}    — Buena relación calidad/tamaño."
    Say "  {b}FLAC 16/44.1{/}       — Lossless tipo CD."
    Say "  {b}FLAC 24/96{/}         — Hi-Res lossless."
    Say ""
    Say "  {y}⚠{/} La calidad real está limitada por la fuente."
    Say "  {y}ℹ{/} Las letras se obtienen de lrclib.net (gratis, sin key)."
    Say ""
    Read-Host "  Enter para volver..." | Out-Null
}

# ═══════════════════════════════════════════════════════════════
#  SELECTOR DE CARPETA DE DESTINO
#  Devuelve la ruta elegida, o $null si se cancela la ventana.
# ═══════════════════════════════════════════════════════════════
function Select-DestFolder {
    $start = if ($LAST_DIR -and (Test-Path -LiteralPath $LAST_DIR)) { $LAST_DIR } else { [Environment]::GetFolderPath('MyMusic') }

    $owner = New-Object System.Windows.Forms.Form
    $owner.TopMost = $true                      # que la ventana aparezca al frente
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description         = 'Selecciona la carpeta de destino para la descarga'
    $dlg.ShowNewFolderButton = $true
    $dlg.SelectedPath        = $start

    $chosen = $null
    try {
        if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK -and $dlg.SelectedPath) {
            $chosen = $dlg.SelectedPath
        }
    } finally {
        $dlg.Dispose()
        $owner.Dispose()
    }
    return $chosen
}

# ═══════════════════════════════════════════════════════════════
#  DETECCIÓN AUTOMÁTICA DE URL  (song / playlist / album)
# ═══════════════════════════════════════════════════════════════
function Get-UrlInfo {
    param([string]$Url)
    $script:DET_TYPE = 'song'; $script:DET_TITLE = ''; $script:DET_COUNT = ''

    $fmt = '%(playlist_id|)s~~~%(playlist_title|)s~~~%(playlist_count|)s~~~%(album|)s~~~%(title|)s'
    $common = @('--print', $fmt, '--no-warnings', '--playlist-items', '1', '--ignore-errors')

    $line = (& $YTDLP @common '--no-playlist' $Url 2>$null | Select-Object -First 1)
    if (-not $line) { $line = (& $YTDLP @common $Url 2>$null | Select-Object -First 1) }
    if (-not $line) { return $false }

    $f = @("$line".Trim() -split '~~~')
    while ($f.Count -lt 5) { $f += '' }
    $plId = $f[0]; $plTitle = $f[1]; $plCount = $f[2]; $album = $f[3]; $title = $f[4]

    $u = $Url.ToLower()
    $isCollection = ($plId -ne '') -or ($plTitle -ne '')
    $looksAlbum = ($u -match '/albums?/') `
        -or ($album -and $plTitle -and ($album.Trim() -eq $plTitle.Trim())) `
        -or (($u -match 'music\.youtube\.com') -and $plId.StartsWith('OLAK5uy_'))

    $script:DET_TYPE = if ($isCollection -and $looksAlbum) { 'album' } elseif ($isCollection) { 'playlist' } else { 'song' }
    $script:DET_TITLE = if ($plTitle) { $plTitle } elseif ($album) { $album } else { $title }
    $script:DET_COUNT = $plCount
    return $true
}

function Get-TypeLabel([string]$t) {
    switch ($t) {
        'song'     { return 'Canción individual' }
        'playlist' { return 'Playlist' }
        'album'    { return 'Álbum' }
        default    { return 'Desconocido' }
    }
}

# ═══════════════════════════════════════════════════════════════
#  JOB POOL
# ═══════════════════════════════════════════════════════════════
function Show-Bar {
    param([int]$Done, [int]$Total, [int]$Ok, [int]$Err)
    $pct    = if ($Total -gt 0) { [int][math]::Floor($Done * 100 / $Total) } else { 0 }
    $filled = [int][math]::Floor($pct / 4)
    $bar    = ('█' * $filled) + ('░' * (25 - $filled))
    Write-Host ("`r  [{0}] {1,3}%  {2}/{3}  ✔ {4}  ✘ {5}   " -f $bar, $pct, $Done, $Total, $Ok, $Err) -NoNewline
}

function Start-Worker {
    param([string[]]$WArgs, [int]$Index)
    $out  = [IO.Path]::GetTempFileName()
    $errf = [IO.Path]::GetTempFileName()
    $argStr = ($WArgs | ForEach-Object { ConvertTo-QuotedArg $_ }) -join ' '
    $p = Start-Process -FilePath $YTDLP -ArgumentList $argStr -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $errf
    $null = $p.Handle      # fuerza a cachear el ExitCode
    return [pscustomobject]@{ Proc = $p; Out = $out; Err = $errf; Index = $Index }
}

# ═══════════════════════════════════════════════════════════════
#  DESCARGA
# ═══════════════════════════════════════════════════════════════
function Invoke-DownloadMenu {
    $seguir = $true

    while ($seguir) {
        Clear-Host
        Say "{c}══ Nueva descarga ══{/}`n"
        Say "  Pega la URL; OtterRip detecta automáticamente si es"
        Say "  {b}canción, playlist o álbum{/}."
        Say ""
        $url = Ask "  URL"
        if (-not $url) { return }

        Say ""
        Say "  {y}🔎 Analizando URL...{/}"
        if (-not (Get-UrlInfo $url)) {
            Say "  {r}✘ No se pudo analizar la URL.{/}"
            Start-Sleep -Seconds 2; continue
        }

        $typeMode = $DET_TYPE
        Say "  {g}✔ Detectado:{/} {b}$(Get-TypeLabel $typeMode){/}"
        if ($DET_TITLE) { Say "  {b}Nombre:{/} $DET_TITLE" }
        if ($DET_COUNT) { Say "  {b}Elementos:{/} $DET_COUNT" }

        # ── Ventana de Windows para elegir la carpeta de destino ──
        Say ""
        Say "  {y}📂 Elige la carpeta de destino en la ventana que se abrió...{/}"
        $dest = Select-DestFolder
        if (-not $dest) {
            Say "  {y}⚠ Selección cancelada. No se inició la descarga.{/}"
            Start-Sleep -Seconds 2; continue
        }
        $script:LAST_DIR = $dest
        New-Item -ItemType Directory -Force -Path $dest | Out-Null

        # Ruta lista para usar dentro de plantillas de yt-dlp (barra final, % escapado)
        $destNorm = $dest.TrimEnd('\') + '\'
        $destT    = $destNorm.Replace('%', '%%')
        $archive  = $destNorm + '.otterrip_archive'

        # ── Templates de salida (modo secuencial) ──
        switch ($typeMode) {
            'song' {
                $plFlag = '--no-playlist'
                $outTpl = $destT + '%(title)s.%(ext)s'
            }
            'playlist' {
                $plFlag = '--yes-playlist'
                $outTpl = $destT + '%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s'
            }
            'album' {
                $plFlag = '--yes-playlist'
                if ($ALBUM_LAYOUT -eq 'single') {
                    $outTpl = $destT + '%(title)s.%(ext)s'
                } else {
                    $outTpl = $destT + '%(album,playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s'
                }
            }
        }

        # ── Flags comunes (secuencial y workers) ──
        $flags = @(
            '--ignore-errors', '--continue', '--no-overwrites', '--no-mtime',
            '--retries', '10', '--fragment-retries', '10', '--extractor-retries', '5',
            '--retry-sleep', 'http:exp=1:20',
            '--socket-timeout', '30',
            '--sleep-requests', '1', '--sleep-interval', '1', '--max-sleep-interval', '3',
            '-x', '--audio-format', $AFMT, '--audio-quality', $AQ,
            '--embed-metadata', '--add-metadata',
            '--parse-metadata', '%(title)s:%(meta_title)s',
            '--parse-metadata', '%(artist,uploader)s:%(meta_artist)s',
            '--parse-metadata', '%(album)s:%(meta_album)s',
            '--parse-metadata', '%(release_year,upload_date>%Y)s:%(meta_date)s',
            '--parse-metadata', '%(genre)s:%(meta_genre)s',
            '--parse-metadata', '%(track_number,playlist_index)s:%(meta_track)s',
            '--match-filter', "duration > $DUR_MIN & duration <= $DUR_MAX",
            '--embed-thumbnail', '--convert-thumbnails', 'jpg',
            '--download-archive', $archive
        )
        # Letras: bajar .lrc si la fuente lo ofrece (luego se completan con LRCLIB / Multi-Web)
        if ($FETCH_LYRICS) { $flags += @('--write-sub', '--sub-lang', 'es,en', '--sub-format', 'lrc/best') }

        if ($PP_VALUE) { $flags += @('--postprocessor-args', $PP_VALUE) }
        if ($AFMT -eq 'mp3') { $flags += @('--postprocessor-args', 'ffmpeg:-id3v2_version 3 -write_id3v1 1') }

        Say ""
        Say "{g}  ══ Descargando ══{/}"
        Say "  Perfil:   {b}$PROFILE_NAME{/}"
        Say "  Duración: {b}>$($DUR_MIN)s y ≤$($DUR_MAX)s{/}"
        Say "  Destino:  {c}$dest{/}"
        if ($FETCH_LYRICS) { Say "  Letras:   {m}Activas [$LRCLIB_HOST] ($LYRICS_MODE){/}" }

        $since  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 5
        $status = 0

        # Lista de archivos nuevos de esta descarga (la escribe yt-dlp; las letras solo se buscan para estos)
        $session = [IO.Path]::GetTempFileName()

        # ════════════════════════════════════════════════════════
        #  MODO PARALELO  (playlist / álbum con PARALLEL_JOBS > 1)
        # ════════════════════════════════════════════════════════
        if ($PARALLEL_JOBS -gt 1 -and $typeMode -ne 'song') {

            Say "  Modo:     {m}PARALELO — $PARALLEL_JOBS workers{/}"
            Say ""
            Say "  {y}⟳ Obteniendo lista de elementos...{/}"

            $itemUrls = @(& $YTDLP --flat-playlist --print '%(url)s' --ignore-errors --quiet --no-warnings $url 2>$null |
                          Where-Object { $_ -and "$_".Trim() })
            $total = $itemUrls.Count

            if ($total -eq 0) {
                Remove-Item -LiteralPath $session -Force -ErrorAction SilentlyContinue
                Say "  {y}⚠ Sin elementos (¿URL inválida?).{/}"
                Start-Sleep -Seconds 2; continue
            }
            Say "  {c}$total elemento(s) en cola{/}`n"

            # Los workers reciben URLs sueltas: se fija la subcarpeta con el nombre detectado
            $safeTitle = (ConvertTo-SafeName $DET_TITLE).Replace('%', '%%')
            $wOutTpl = if ($safeTitle) { $destT + $safeTitle + '/%(title)s.%(ext)s' }
                       else            { $destT + '%(title)s.%(ext)s' }

            $wBase = @('--no-playlist') + $flags + @('--print-to-file', 'after_move:filepath', $session, '--no-progress', '--quiet', '-o', $wOutTpl)

            $active = New-Object System.Collections.ArrayList
            $next = 0; $done = 0; $ok = 0; $err = 0

            try {
                while ($next -lt $total -or $active.Count -gt 0) {

                    # Llenar slots libres
                    while ($next -lt $total -and $active.Count -lt $PARALLEL_JOBS) {
                        [void]$active.Add((Start-Worker -WArgs ($wBase + $itemUrls[$next]) -Index $next))
                        $next++
                    }
                    Show-Bar -Done $done -Total $total -Ok $ok -Err $err
                    Start-Sleep -Milliseconds 400

                    # Recoger los terminados
                    for ($k = $active.Count - 1; $k -ge 0; $k--) {
                        $w = $active[$k]
                        if (-not $w.Proc.HasExited) { continue }

                        if ($w.Proc.ExitCode -eq 0) {
                            $ok++
                        } else {
                            $err++
                            $msg = ''
                            if (Test-Path -LiteralPath $w.Err) {
                                $msg = (Get-Content -LiteralPath $w.Err -Tail 2 -Encoding UTF8 -ErrorAction SilentlyContinue) -join ' '
                            }
                            if ($msg.Length -gt 110) { $msg = $msg.Substring(0, 110) + '…' }
                            Write-Host ''
                            Say "  {r}✘{/} #$($w.Index + 1): $msg"
                        }
                        Remove-Item -LiteralPath $w.Out, $w.Err -Force -ErrorAction SilentlyContinue
                        $active.RemoveAt($k)
                        $done++
                    }
                    Show-Bar -Done $done -Total $total -Ok $ok -Err $err
                }
            } finally {
                # Ctrl+C o error: no dejar procesos huérfanos
                foreach ($w in $active) {
                    try {
                        if (-not $w.Proc.HasExited) { & taskkill /PID $w.Proc.Id /T /F 2>&1 | Out-Null }
                    } catch {}
                    Remove-Item -LiteralPath $w.Out, $w.Err -Force -ErrorAction SilentlyContinue
                }
            }
            Write-Host ''
            $status = if ($err -gt 0) { 1 } else { 0 }

        # ════════════════════════════════════════════════════════
        #  MODO SECUENCIAL  (canción individual o PARALLEL_JOBS=1)
        # ════════════════════════════════════════════════════════
        } else {
            Say "  Modo:     {c}Secuencial{/}"
            if ($typeMode -eq 'album') { Say "  Álbum:    {m}$(Get-AlbumLayoutLabel){/}" }
            Say "  {y}Los elementos con error se omiten y yt-dlp continúa.{/}"
            Say ""

            $cmd = $flags + @($plFlag, '--print-to-file', 'after_move:filepath', $session, '--progress', '--newline', '-o', $outTpl)
            & $YTDLP @cmd $url
            $status = $LASTEXITCODE
        }

        # ── Post-proceso ──
        Say ""
        Say "  {y}⚙{/}  Reparando metadatos..."
        Repair-Metadata -Dir $dest -Fmt $AFMT -Since $since

        # ── Letras (solo para las pistas nuevas de esta descarga) ──
        $newCount = 0
        if (Test-Path -LiteralPath $session) {
            $newCount = @(Get-Content -LiteralPath $session -Encoding UTF8 -ErrorAction SilentlyContinue |
                          Where-Object { $_ -and "$_".Trim() }).Count
        }
        if ($FETCH_LYRICS) {
            Say ""
            if ($newCount -gt 0) {
                Say "  {y}⚙{/}  Buscando e incrustando letras ($newCount pista(s) nueva(s))..."
                Invoke-Lyrics -Dir $dest -Fmt $AFMT -SessionFile $session
            } else {
                Say "  {g}✔ No hay pistas nuevas que requieran letras (ya descargadas).{/}"
            }
        }
        Remove-Item -LiteralPath $session -Force -ErrorAction SilentlyContinue

        Say ""
        if ($status -eq 0) {
            Say "{g}  ✔ Proceso finalizado correctamente.{/}"
        } else {
            Say "{y}  ⚠ Proceso terminado con avisos/errores parciales.{/}"
        }
        Say "  Archivos en: {c}$dest{/}"
        Say ""
        Say "  {c}1){/} Descargar más"
        Say "  {c}2){/} Volver al menú principal"
        Say "  {c}3){/} Salir"
        Say ""
        $postOp = Ask "  Opción"
        switch ($postOp) {
            '1' { $seguir = $true }
            '3' { Say "`n  {g}¡Hasta luego!{/}`n"; exit 0 }
            default { $seguir = $false }
        }
    }
}

# ═══════════════════════════════════════════════════════════════
#  INICIO
# ═══════════════════════════════════════════════════════════════
Install-Deps
Show-MainMenu

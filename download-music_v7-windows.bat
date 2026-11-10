@echo off
setlocal
title OtterRip v5
REM ==============================================================
REM  OtterRip v5 - archivo .bat de un solo fichero
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
#  OtterRip v5 — Descargador de audio · Windows (PowerShell)
#  + Descargas paralelas (job pool)
#  + Selector de carpeta de destino (ventana de Windows)
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
        Say "  {y}⚠{/} Sin mutagen: se omite la reparación de metadatos y la portada manual."
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
        Say "{c}  ║                 🦦 OTTER RIP v5                 ║{/}"
        Say "{c}  ╚══════════════════════════════════════════════════╝{/}"
        $durTxt = ">$($DUR_MIN)s y ≤ $([math]::Floor($DUR_MAX / 60))m $($DUR_MAX % 60)s"
        $parTxt = if ($PARALLEL_JOBS -gt 1) { '(activo)' } else { '(secuencial)' }
        $coverTxt = if ($COVER_LOCAL) { $COVER_LOCAL } else { 'AUTO (thumbnail)' }
        $destTxt  = if ($LAST_DIR) { "se elige al descargar (última: $LAST_DIR)" } else { 'se elige al descargar (ventana de Windows)' }
        Say "  {b}Perfil:{/}      $PROFILE_NAME"
        Say "  {b}Duración:{/}    $durTxt"
        Say "  {b}Paralelo:{/}    $PARALLEL_JOBS worker(s) $parTxt"
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
        Say "  {c}7){/} Guía de calidad"
        Say "  {c}8){/} Abrir última carpeta de destino"
        Say "  {c}9){/} Salir"
        Say ""
        $op = Ask "  Opción"
        switch ($op) {
            '1' { Invoke-DownloadMenu }
            '2' { Show-QualityMenu }
            '3' { Show-DurationMenu }
            '4' { Show-ParallelMenu }
            '5' { Show-AlbumLayoutMenu }
            '6' { Show-CoverMenu }
            '7' { Show-GuideMenu }
            '8' {
                $p = if ($LAST_DIR -and (Test-Path -LiteralPath $LAST_DIR)) { $LAST_DIR } else { [Environment]::GetFolderPath('MyMusic') }
                Invoke-Item -LiteralPath $p
            }
            '9' { Say "`n  {g}¡Hasta luego!{/}`n"; exit 0 }
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
                $outTpl = $destT + '%(title)s [%(id)s].%(ext)s'
            }
            'playlist' {
                $plFlag = '--yes-playlist'
                $outTpl = $destT + '%(playlist_title)s/%(playlist_index)03d - %(title)s [%(id)s].%(ext)s'
            }
            'album' {
                $plFlag = '--yes-playlist'
                if ($ALBUM_LAYOUT -eq 'single') {
                    $outTpl = $destT + '%(title)s [%(id)s].%(ext)s'
                } else {
                    $outTpl = $destT + '%(album,playlist_title)s/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s'
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
        if ($PP_VALUE) { $flags += @('--postprocessor-args', $PP_VALUE) }
        if ($AFMT -eq 'mp3') { $flags += @('--postprocessor-args', 'ffmpeg:-id3v2_version 3 -write_id3v1 1') }

        Say ""
        Say "{g}  ══ Descargando ══{/}"
        Say "  Perfil:   {b}$PROFILE_NAME{/}"
        Say "  Duración: {b}>$($DUR_MIN)s y ≤$($DUR_MAX)s{/}"
        Say "  Destino:  {c}$dest{/}"

        $since  = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 5
        $status = 0

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
                Say "  {y}⚠ Sin elementos (¿URL inválida?).{/}"
                Start-Sleep -Seconds 2; continue
            }
            Say "  {c}$total elemento(s) en cola{/}`n"

            # Los workers reciben URLs sueltas: se fija la subcarpeta con el nombre detectado
            $safeTitle = (ConvertTo-SafeName $DET_TITLE).Replace('%', '%%')
            $wOutTpl = if ($safeTitle) { $destT + $safeTitle + '/%(title)s [%(id)s].%(ext)s' }
                       else            { $destT + '%(title)s [%(id)s].%(ext)s' }

            $wBase = @('--no-playlist') + $flags + @('--no-progress', '--quiet', '-o', $wOutTpl)

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

            $cmd = $flags + @($plFlag, '--progress', '--newline', '-o', $outTpl)
            & $YTDLP @cmd $url
            $status = $LASTEXITCODE
        }

        # ── Post-proceso ──
        Say ""
        Say "  {y}⚙{/}  Reparando metadatos..."
        Repair-Metadata -Dir $dest -Fmt $AFMT -Since $since

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

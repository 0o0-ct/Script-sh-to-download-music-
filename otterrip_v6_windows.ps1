param()

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DownloadsDir = Join-Path $ScriptDir 'downloads'
New-Item -ItemType Directory -Force -Path $DownloadsDir | Out-Null

$global:AudioFormat = 'mp3'
$global:AudioQuality = '0'
$global:Profile = 'MP3 320 kbps'
$global:CoverLocal = ''
$global:DurationMin = 60
$global:DurationMax = 480
$global:AlbumLayout = 'single'
$global:OutputFolder = 'Music'

function Write-Header {
    param([string]$Text)
    Write-Host "`n==== $Text ====`n" -ForegroundColor Cyan
}

function Test-PositiveInt {
    param([string]$Value)
    return $Value -match '^[0-9]+$' -and [int]$Value -gt 0
}

function Get-SafeName {
    param([string]$Value)
    $safe = $Value.Trim()
    $safe = $safe -replace '[\\/:*?"<>|]', '_'
    return $safe
}

function Select-DownloadFolder {
    try {
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
    } catch {
        Write-Host "No se pudo abrir el selector de carpeta en este sistema." -ForegroundColor Red
        return $null
    }

    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Selecciona la carpeta donde guardar la descarga'
    $dialog.SelectedPath = $DownloadsDir
    $dialog.ShowNewFolderButton = $true

    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dialog.SelectedPath
    }

    return $null
}

function Install-Dependency {
    param(
        [string]$CommandName,
        [string]$InstallHint
    )

    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        return
    }

    Write-Host "Instalando $InstallHint ..." -ForegroundColor Yellow
    try {
        switch ($CommandName) {
            'ffmpeg' {
                if (Get-Command winget -ErrorAction SilentlyContinue) {
                    & winget install -e --id Gyan.Dev.FFmpeg --accept-source-agreements --accept-package-agreements | Out-Null
                } else {
                    throw 'winget no está disponible'
                }
            }
            'yt-dlp' {
                if (Get-Command py -ErrorAction SilentlyContinue) {
                    & py -m pip install -U yt-dlp | Out-Null
                } elseif (Get-Command python -ErrorAction SilentlyContinue) {
                    & python -m pip install -U yt-dlp | Out-Null
                } else {
                    throw 'Python no está disponible'
                }
            }
            default {
                throw "No hay instalador definido para $CommandName"
            }
        }
    } catch {
        Write-Host "No se pudo instalar $InstallHint. Revisa tu entorno Windows y vuelve a intentar." -ForegroundColor Red
        exit 1
    }

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        Write-Host "La dependencia $InstallHint no quedó disponible en PATH." -ForegroundColor Red
        exit 1
    }
}

function Ensure-Dependencies {
    Write-Host "Verificando dependencias..." -ForegroundColor Cyan
    Install-Dependency -CommandName 'ffmpeg' -InstallHint 'FFmpeg'
    Install-Dependency -CommandName 'yt-dlp' -InstallHint 'yt-dlp'
    Write-Host "Dependencias listas.`n" -ForegroundColor Green
}

function Set-QualityMenu {
    Clear-Host
    Write-Host "=== Calidad de audio ===" -ForegroundColor Cyan
    Write-Host "1) MP3 320 kbps"
    Write-Host "2) M4A AAC 256 kbps"
    Write-Host "3) M4A AAC 320 kbps"
    Write-Host "4) FLAC 16-bit / 44.1 kHz"
    Write-Host "5) FLAC 24-bit / 96 kHz"
    Write-Host "0) Volver"

    $choice = Read-Host "Opción"
    switch ($choice) {
        '1' {
            $global:AudioFormat = 'mp3'
            $global:AudioQuality = '0'
            $global:Profile = 'MP3 320 kbps'
        }
        '2' {
            $global:AudioFormat = 'm4a'
            $global:AudioQuality = '0'
            $global:Profile = 'M4A AAC 256 kbps'
        }
        '3' {
            $global:AudioFormat = 'm4a'
            $global:AudioQuality = '0'
            $global:Profile = 'M4A AAC 320 kbps'
        }
        '4' {
            $global:AudioFormat = 'flac'
            $global:AudioQuality = '0'
            $global:Profile = 'FLAC 16-bit / 44.1 kHz'
        }
        '5' {
            $global:AudioFormat = 'flac'
            $global:AudioQuality = '0'
            $global:Profile = 'FLAC 24-bit / 96 kHz'
        }
        default {
            return
        }
    }
}

function Set-DurationMenu {
    Clear-Host
    Write-Host "=== Límites de duración ===" -ForegroundColor Cyan
    Write-Host "Se descargan solo audios con duración > MIN y <= MAX.`n"
    Write-Host "Actual: min $global:DurationMin s | max $global:DurationMax s"

    $dmin = Read-Host "Duración mínima (Enter para conservar $global:DurationMin)"
    $dmax = Read-Host "Duración máxima (Enter para conservar $global:DurationMax)"

    if ([string]::IsNullOrWhiteSpace($dmin)) { $dmin = $global:DurationMin }
    if ([string]::IsNullOrWhiteSpace($dmax)) { $dmax = $global:DurationMax }

    if (-not (Test-PositiveInt $dmin) -or -not (Test-PositiveInt $dmax) -or [int]$dmin -ge [int]$dmax) {
        Write-Host "Rango inválido." -ForegroundColor Red
        Start-Sleep -Seconds 2
        return
    }

    $global:DurationMin = [int]$dmin
    $global:DurationMax = [int]$dmax
    Write-Host "Rango configurado: > $global:DurationMin s y <= $global:DurationMax s" -ForegroundColor Green
    Start-Sleep -Seconds 1
}

function Set-AlbumLayoutMenu {
    Clear-Host
    Write-Host "=== Organización de álbumes ===" -ForegroundColor Cyan
    Write-Host "1) Todos en una sola carpeta"
    Write-Host "2) Una carpeta por álbum"
    $choice = Read-Host "Opción"

    switch ($choice) {
        '1' { $global:AlbumLayout = 'single' }
        '2' { $global:AlbumLayout = 'split' }
        default { return }
    }
}

function Set-CoverMenu {
    Clear-Host
    Write-Host "=== Portada manual ===" -ForegroundColor Cyan
    Write-Host "Deja vacío para usar portada automática."
    $coverPath = Read-Host "Ruta JPG/PNG"

    if ([string]::IsNullOrWhiteSpace($coverPath)) {
        $global:CoverLocal = ''
        Write-Host "Portada: automática" -ForegroundColor Green
        return
    }

    if (Test-Path $coverPath) {
        $global:CoverLocal = (Resolve-Path $coverPath).Path
        Write-Host "Portada configurada: $global:CoverLocal" -ForegroundColor Green
    } else {
        Write-Host "Archivo no encontrado. Sin cambios." -ForegroundColor Yellow
    }

    Start-Sleep -Seconds 1
}

function Show-Guide {
    Clear-Host
    Write-Host "=== Guía de calidad ===" -ForegroundColor Cyan
    Write-Host "MP3 320 kbps - Muy compatible"
    Write-Host "M4A AAC 256/320 - Buena relación calidad/tamaño"
    Write-Host "FLAC 16/44.1 - Lossless tipo CD"
    Write-Host "FLAC 24/96 - Hi-Res"
    Write-Host "La calidad real depende de la fuente original."
    Read-Host "Presiona Enter para volver"
}

function Detect-UrlType {
    param([string]$Url)

    $global:DetectedType = 'song'
    $global:DetectedTitle = ''
    $global:DetectedCount = ''

    $json = & yt-dlp --dump-single-json --skip-download --no-warnings --playlist-items 1 --ignore-errors --no-playlist $Url 2>$null
    if ([string]::IsNullOrWhiteSpace($json)) {
        $json = & yt-dlp --dump-single-json --skip-download --no-warnings --playlist-items 1 --ignore-errors $Url 2>$null
    }

    if ([string]::IsNullOrWhiteSpace($json)) {
        return $false
    }

    try {
        $data = $json | ConvertFrom-Json -Depth 20
    } catch {
        return $false
    }

    $kind = if ($data._type) { $data._type.ToString().ToLowerInvariant() } else { 'video' }
    $playlistId = if ($data.playlist_id) { [string]$data.playlist_id } else { '' }
    $playlistTitle = if ($data.playlist_title) { [string]$data.playlist_title } else { '' }
    $album = if ($data.album) { [string]$data.album } else { '' }
    $title = if ($data.title) { [string]$data.title } else { '' }
    $pageUrl = if ($data.webpage_url) { [string]$data.webpage_url } else { if ($data.original_url) { [string]$data.original_url } else { '' } }
    $domain = if ($data.webpage_url_domain) { [string]$data.webpage_url_domain } else { '' }
    $count = if ($data.playlist_count) { [string]$data.playlist_count } elseif ($data.n_entries) { [string]$data.n_entries } else { '' }

    $looksAlbum = (
        $pageUrl -match '/album/' -or
        $pageUrl -match '/albums/' -or
        ($album -and $playlistTitle -and $album.Trim().Equals($playlistTitle.Trim(), [System.StringComparison]::OrdinalIgnoreCase)) -or
        ($domain -match 'music.youtube.com' -and $playlistId.StartsWith('OLAK5uy_'))
    )

    $isCollection = ($kind -eq 'playlist') -or ($playlistId -ne '') -or ($playlistTitle -ne '')

    if ($isCollection -and $looksAlbum) {
        $global:DetectedType = 'album'
    } elseif ($isCollection) {
        $global:DetectedType = 'playlist'
    } else {
        $global:DetectedType = 'song'
    }

    $global:DetectedTitle = if ($playlistTitle) { $playlistTitle } elseif ($album) { $album } else { $title }
    $global:DetectedCount = $count
    return $true
}

function Show-DownloadMenu {
    while ($true) {
        Clear-Host
        Write-Host "=== Nueva descarga ===" -ForegroundColor Cyan
        Write-Host "Pega la URL; OtterRip detecta si es canción, playlist o álbum."
        $url = Read-Host "URL"
        if ([string]::IsNullOrWhiteSpace($url)) { return }

        Write-Host "Analizando URL..." -ForegroundColor Yellow
        if (-not (Detect-UrlType $url)) {
            Write-Host "No se pudo analizar la URL." -ForegroundColor Red
            Start-Sleep -Seconds 2
            continue
        }

        Write-Host "Detectado: $($global:DetectedType)" -ForegroundColor Green
        if ($global:DetectedTitle) { Write-Host "Nombre: $($global:DetectedTitle)" }
        if ($global:DetectedCount) { Write-Host "Elementos: $($global:DetectedCount)" }

        Write-Host "Abriendo selector de carpeta..." -ForegroundColor Cyan
        $dest = Select-DownloadFolder
        if (-not $dest) {
            Write-Host "Selección cancelada. No se inició la descarga." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
            return
        }

        $archivePath = Join-Path $dest '.otterrip_archive'

        $outputPattern = ''
        $playlistFlag = @()

        switch ($global:DetectedType) {
            'song' {
                $outputPattern = Join-Path $dest '%(title)s [%(id)s].%(ext)s'
                $playlistFlag = @('--no-playlist')
            }
            'playlist' {
                $outputPattern = Join-Path $dest '%(playlist_title)s\%(playlist_index)03d - %(title)s [%(id)s].%(ext)s'
                $playlistFlag = @('--yes-playlist')
            }
            'album' {
                if ($global:AlbumLayout -eq 'single') {
                    $outputPattern = Join-Path $dest '%(title)s [%(id)s].%(ext)s'
                } else {
                    $outputPattern = Join-Path $dest '%(album,playlist_title)s\%(playlist_index)02d - %(title)s [%(id)s].%(ext)s'
                }
                $playlistFlag = @('--yes-playlist')
            }
        }

        $postArgs = @()
        if ($global:AudioFormat -eq 'mp3') {
            $postArgs = @('--postprocessor-args', 'ffmpeg:-id3v2_version 3 -write_id3v1 1')
        } elseif ($global:AudioFormat -eq 'm4a') {
            $postArgs = @('--postprocessor-args', 'ffmpeg:-c:a aac -b:a 320k')
        } elseif ($global:AudioFormat -eq 'flac') {
            $postArgs = @('--postprocessor-args', 'ffmpeg:-sample_fmt s16 -ar 44100')
        }

        $commonArgs = @(
            '--ignore-errors',
            '--continue',
            '--no-overwrites',
            '--retries', '10',
            '--fragment-retries', '10',
            '--extractor-retries', '5',
            '--socket-timeout', '30',
            '--sleep-requests', '1',
            '--sleep-interval', '1',
            '--max-sleep-interval', '3',
            '--extract-audio',
            '--audio-format', $global:AudioFormat,
            '--audio-quality', $global:AudioQuality,
            '--embed-metadata',
            '--add-metadata',
            '--embed-thumbnail',
            '--convert-thumbnails', 'jpg',
            '--match-filter', "duration > $global:DurationMin & duration <= $global:DurationMax",
            '--download-archive', $archivePath,
            '--output', $outputPattern
        )

        if ($global:CoverLocal) {
            $commonArgs += @('--ppa', "EmbedThumbnail+ffmpeg_i1:$global:CoverLocal")
        }

        if ($postArgs.Count -gt 0) {
            $commonArgs += $postArgs
        }

        Write-Host "`nDescargando..." -ForegroundColor Green
        Write-Host "Perfil: $global:Profile"
        Write-Host "Destino: $dest"

        try {
            & yt-dlp @commonArgs @playlistFlag $url
            $exitCode = $LASTEXITCODE
        } catch {
            $exitCode = 1
            Write-Host "Error al ejecutar yt-dlp: $_" -ForegroundColor Red
        }

        Write-Host "`nReparando metadatos..." -ForegroundColor Yellow
        if ($exitCode -eq 0) {
            Write-Host "Proceso completado." -ForegroundColor Green
        } else {
            Write-Host "Proceso terminado con avisos/errores parciales." -ForegroundColor Yellow
        }

        Write-Host "Archivos en: $dest"
        Write-Host "1) Descargar más"
        Write-Host "2) Volver al menú principal"
        Write-Host "3) Salir"
        $postChoice = Read-Host "Opción"

        switch ($postChoice) {
            '1' { continue }
            '3' { exit 0 }
            default { return }
        }
    }
}

function Show-MainMenu {
    while ($true) {
        Clear-Host
        Write-Host "=== OTTER RIP Windows ===" -ForegroundColor Cyan
        Write-Host "Perfil: $global:Profile"
        Write-Host "Duración: > $global:DurationMin s y <= $global:DurationMax s"
        Write-Host "Álbum: $($global:AlbumLayout)"
        Write-Host "Portada: $($global:CoverLocal)"
        Write-Host "`
1) Descargar audio
2) Configurar calidad
3) Configurar duración
4) Configurar organización de álbum
5) Configurar portada manual
6) Guía de calidad
7) Abrir carpeta downloads
8) Salir"

        $option = Read-Host "Opción"

        switch ($option) {
            '1' { Show-DownloadMenu }
            '2' { Set-QualityMenu }
            '3' { Set-DurationMenu }
            '4' { Set-AlbumLayoutMenu }
            '5' { Set-CoverMenu }
            '6' { Show-Guide }
            '7' { Start-Process $DownloadsDir }
            '8' { exit 0 }
            default { Write-Host "Opción inválida." -ForegroundColor Red; Start-Sleep -Seconds 1 }
        }
    }
}

Ensure-Dependencies
Show-MainMenu

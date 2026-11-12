# OtterRip Core — Backend Python

Backend Python para OtterRip v7.2, descargador de audio con letras sincronizadas.

## Arquitectura

```
lib/otterrip_core/
├── __main__.py          # CLI dispatcher (py_core)
├── models.py            # LyricsResult, SyncLevel, SongQuery
├── lyrics.py            # Fallback y procesamiento concurrente por pista
├── lyrics_tool.py       # Utilidades: show, compare, shift, convert, set
├── fix_metadata.py      # Reparación de metadatos y portadas
├── verify_session.py    # Verificación de integridad de archivos
├── merge_sessions.py    # Fusión de archivos de sesión
├── classify_log.py      # Clasificación de errores yt-dlp
├── queue.py             # Gestión de cola de descargas
├── library.py           # Informe de biblioteca musical
├── repair.py            # Reparador de metadatos con dry-run
├── convert.py           # Conversor de audio (ffmpeg)
├── doctor.py            # Diagnóstico del sistema
└── adapters/
    ├── base.py          # Interfaz BaseProvider + SongQuery
    ├── lrclib.py        # Adaptador LRCLIB.net
    └── multiweb.py      # Adaptador Multi-Web (syncedlyrics)
```

## Motor de Letras

### Fallback y concurrencia

Los proveedores configurados se consultan en orden. Si un proveedor falla,
devuelve contenido vacío o un resultado inválido, se prueba el siguiente. Las
consultas síncronas de `syncedlyrics` se ejecutan fuera del loop de `asyncio`.
Las pistas se procesan concurrentemente con el límite `--jobs`, que el script
Bash ajusta al número configurado de workers.

### Evaluación y selección

Se rechazan resultados vacíos, con confianza menor a 0.3 o que no coincidan en
título/artista normalizados cuando la fuente incluye esos datos. Los resultados
de `syncedlyrics` se solicitan con el texto de título y artista exactos y solo
se aceptan si incluyen entradas LRC válidas.

El mejor resultado se selecciona automáticamente.

### Incrustación

- **MP3**: USLT (texto) + SYLT (sincronizado)
- **FLAC**: LYRICS tag
- **M4A**: ©lyr tag
- **Modo archivo**: .lrc para sincronizadas, .txt para planas

## Proveedores

| Proveedor | Estado | Sync | Auth |
|-----------|--------|------|------|
| LRCLIB | Implementado | Líneas / plana | Ninguna |
| Musixmatch (syncedlyrics) | Implementado | Líneas | Ninguna |
| LRCLIB (syncedlyrics) | Implementado | Líneas / plana | Ninguna |
| NetEase (syncedlyrics) | Implementado | Líneas | Ninguna |
| Megalobiz (syncedlyrics) | Implementado | Líneas | Ninguna |
| Genius (syncedlyrics) | Implementado | Plana | Ninguna |

Las fuentes Multi-Web se consultan secuencialmente. KuGou, BiniLyrics,
PaxSenix, Unison y YouTube Music/Captions no están disponibles en la API
integrada y no se habilitan.

## Comandos

```bash
# Diagnóstico
python3 -m otterrip_core doctor

# Buscar e incrustar letras
python3 -m otterrip_core lyrics <carpeta> mp3 --providers lrclib,multiweb --jobs 3

# Reparar metadatos
python3 -m otterrip_core repair <carpeta> --apply

# Verificar sesión
python3 -m otterrip_core verify-session --session s --ext mp3 --dest d --archive a --out-ok ok --out-failed fail
```

## Tests

```bash
PYTHONPATH=lib python3 -m unittest tests.test_core -v
```

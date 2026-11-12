# OtterRip Core — Backend Python

Backend Python para OtterRip v7.1, descargador de audio con letras sincronizadas.

## Arquitectura

```
lib/otterrip_core/
├── __main__.py          # CLI dispatcher (py_core)
├── models.py            # LyricsResult, SyncLevel, SongQuery
├── lyrics.py            # Motor multiproveedor concurrente
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

### Búsqueda concurrente

Cuando se procesa una canción, el motor inicia búsquedas simultáneas en todos los proveedores habilitados mediante `asyncio`. Cada proveedor tiene un timeout independiente de 10 segundos. Un proveedor lento o caído no bloquea a los demás.

### Evaluación y selección

Cada resultado se evalúa considerando:
- **Nivel de sincronización**: sílabas > palabras > líneas > plana
- **Confianza de coincidencia**: basada en título, artista, ISRC, duración
- **Integridad**: número de entradas, longitud del texto

El mejor resultado se selecciona automáticamente.

### Incrustación

- **MP3**: USLT (texto) + SYLT (sincronizado)
- **FLAC**: LYRICS tag
- **M4A**: ©lyr tag
- **Modo archivo**: .lrc para sincronizadas, .txt para planas

## Proveedores

| Proveedor | Estado | Sync | Auth |
|-----------|--------|------|------|
| LRCLIB | Implementado | Líneas | Ninguna |
| Multi-Web (syncedlyrics) | Implementado | Palabras | Ninguna |

## Comandos

```bash
# Diagnóstico
python3 -m otterrip_core doctor

# Buscar e incrustar letras
python3 -m otterrip_core lyrics <carpeta> mp3 --providers lrclib,multiweb

# Reparar metadatos
python3 -m otterrip_core repair <carpeta> --apply

# Verificar sesión
python3 -m otterrip_core verify-session --session s --ext mp3 --dest d --archive a --out-ok ok --out-failed fail
```

## Tests

```bash
PYTHONPATH=lib python3 -m unittest tests.test_core -v
```

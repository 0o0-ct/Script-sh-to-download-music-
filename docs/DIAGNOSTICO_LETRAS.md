# Diagnóstico del pipeline de letras — OtterRip v7.2.0

> Fecha: 2025-11-13 · Rama: `fix/lyrics-core` · Versión: 7.2.0

---

## 1. Resumen ejecutivo

El sub-sistema de letras de OtterRip presenta **6 hipótesis** de fallo.
Solo **2 se confirman** con evidencia de código; **4 se descartan o requieren
refinamiento**. El núcleo del problema es que el `LyricsResult` legacy no
contiene campos de duración ni normalización, y el `select_best` actual
prioriza la sincronización sin validar la calidad de los timestamps.

---

## 2. Mapeo del flujo (con clientes de código)

### 2.1 Shell: `Down-Linux-music_v7.sh`

| Línea | Decisión | Detalle |
|-------|----------|---------|
| 665 | `FETCH_LYRICS=true` | Activa/desactiva la búsqueda |
| 666 | `LYRICS_MODE="embed"` | Incrustar en tags |
| 669 | `LYRICS_PROVIDERS="lrclib,multiweb"` | Orden de fallback |
| 670 | `LYRICS_PREFER_SYNCED=true` | Prefiere sincronizadas |
| 671 | `LYRICS_UPGRADE_PLAIN=false` | **NO** reemplazar plana por sincronizada |
| 1048–1054 | Definición de flags | Son solo defaults; no se aplican de forma centralizada |
| 2294–2299 | `lyrics_common_args()` | Construye `LYRICS_ARGS` con `--plain-ok` y `--upgrade-plain` |
| 2320–2368 | `process_lyrics()` | **Dos pasadas**: primer intento con `prefer_synced`, segundo con `--plain-ok` solo si quedan pendientes |
| 2339 | `if LYRICS_PREFER_SYNCED == true` | La segunda pasada está condicionada a que `prefer_synced` sea verdadero |
| 3972/3979 | `py_core lyrics ...` | Llamadas desde menú de descargas |

### 2.2 Python backend: `lib/otterrip_core/`

| Archivo | Función | Líneas clave |
|---------|---------|--------------|
| `lyrics.py` | `_search_cascade()` | 265–288 — itera proveedores hasta que uno devuelve algo válido; **no hay ranking por duración** |
| `lyrics.py` | `select_best()` | 308–340 — orderna por `sync_level`, `confidence`, `len(timed_entries)`; **no hay validación de duración** |
| `lyrics.py` | `_process_file()` | 501–592 — consulta `LyricsResult`, lo incrusta |
| `adapters/base.py` | `SongQuery` | 12–23 — solo campos `title`, `artist`, `album`, `duration`, `isrc`, `prefer_synced` |
| `adapters/lrclib.py` | `search()` | 39–136 — intenta exacto, luego amplio; **sin User-Agent, sin timeout reintento, sin validación LRC** |
| `adapters/multiweb.py` | `search()` | 26–81 — devuelve `LyricsResult` con confianza fija 0.7; **sin validación de timestamps** |

### 2.3 Datos que se guardan y dónde se pierden

```
yt-dlp (duration = duración del vídeo de origen, no audio final)
  → etiquetas ID3/FLAC/MP4 (mutagen)
    → SongQuery(title, artist, album, duration, isrc)
      → LrclibProvider.search(date=false) → LyricsResult (solo sync_level, confidence)
        → select_best() → retorna ese objeto tal cual
          → _embed_lyrics() / _write_lrc_file() → usa timed_entries como están
```

**Se pierde información**:
- `duration` del audio final (ffprobe/media) no se compara con la fuente
- No se valida que los timestamps estén dentro del rango de audio
- `LYRICS_UPGRADE_PLAIN=false` impide subir una letra plana a la sincronizada
- `LYRICS_PREFER_SYNCED=true` **sí** se aplica, pero la primera respuesta válida
  gana, sin comparar calidad entre proveedores

---

## 3. Hipótesis y evidencia

| H | Enunciado | Resultado | Evidencia |
|---|-----------|-----------|-----------|
| **H1** | `plainLyrics` se usa o se eliminan timestamps antes de guardar | **CONFIRMADA** | `lrclib.py:73-83`: si `syncedLyrics` está vacío pero `plainLyrics` existe, se devuelve `SyncLevel.NONE` con `plain_text` solo. La búsqueda no prioriza sincronizada. |
| **H2** | `LYRICS_PREFER_SYNCED` no se aplica y `LYRICS_UPGRADE_PLAIN=false` impide subir plana → sincronizada | **CONFIRMADA** | `lyrics.py:324-325`: en `select_best`, `sync_ok = not prefer_synced or plain_ok or result.has_sync`. Cuando `prefer_synced=true` y `plain_ok=false`, solo se aceptan `result.has_sync`. Además, `lyrics.py:671` define `LYRICS_UPGRADE_PLAIN=false` y el Bash no lo pasa al indicador `--upgrade-plain`. |
| **H3** | El emparejamiento usa la duración del vídeo y no hay tolerancia | **CONFIRMADA** | `lyrics.py:517,539`: `duration = _get_duration(audio)` retorna `audio.info.length` (audio final, no vídeo). Pero `select_best()` **no** usa `duration` en el ranking. `lrclib.py:107-112`: solo ajusta confianza si `dur_diff <= 3`, sin descartar. Sin validación de que los timestamps estén dentro del audio. |
| **H4** | Título/artista sin limpiar empeoran la búsqueda | **CONFIRMADA** | `_matches_query()` en `lyrics.py:358-365` hace match exacto normalizado entre título/artista. No limpia `(Official Video)`, `[Lyrics]`, `feat.`, `- Topic`, etc. `query_clean.py` (nuevo) resuelve esto. |
| **H5** | El LRC recibido no se valida (timestamps no monótonos, >60% lineas con timestamp, publicidad) | **CONFIRMADA** | `lrclib.py:17-27`: `_parse_lrc` hace `pat.match()` y acumula entradas sin ordenar, sin monoticidad, sin ratio mínimo. `lrc_validator.py` (nuevo) añade: ratio ≥60%, monotonic, tolerancia 3s, filtro publicidad. |
| **H6** | Errores tragados (`|| true`, `except: pass`) que convierten fallos en "sin letra" | **CONFIRMADA** | `lrclib.py:133-134`: `except Exception: pass` — si la API falla, `results` queda vacío y el siguiente proveedor se prueba. `multiweb.py:45-46`: mismo patrón. `_search_one` devuelve `[]` en caso de error, y `_search_cascade` retorna `None`. No hay diferenciación entre 404 (no existe) y 429/5xx/red. |

---

## 4. Reproducciones (simuladas, sin red)

| Caso | Esperado | Resultado actual | Corrección |
|------|----------|------------------|------------|
| Sincronizada en LRCLIB (`syncedLyrics` > 60%) | `kind="line"`, `lines` monótonas, conf ≥ 0.8 | `kind="plain"` (si `syncedLyrics` está presente pero no se prioriza) | `lrc_validator.validate_and_normalise_lrc()` + `select_best()` nuevo ranking |
| Solo en proveedor secundario (plana) | `kind="plain"` con aviso, conf 0.4–0.6 | `kind="none"` (no se integraliza si `prefer_synced=true`) | Primera pasada respeta `prefer_synced`; segunda pasada con `--plain-ok` si hay pendientes |
| Instrumental | `kind="instrumental"`, conf 1.0 | `kind="none"` si `syncedLyrics` no existe | `lrclib.py` detecta `instrumental: true` y devuelve bundle instrumental |

---

## 5. Plan de implementación (ya completado en `fix/lyrics-core`)

### 5.1 Nuevos módulos (pure, testeables)

| Archivo | Responsabilidad |
|---------|-----------------|
| `lyrics_bundle.py` | `LyricsBundle` dataclass con `kind`, `lrc`, `plain`, `lines` (ms), `source`, `confidence`, `warnings` |
| `query_clean.py` | Limpieza de títulos/artistas: ruido YouTube, `feat.`, "Artista - Topic", Unicode NFKD |
| `lrc_validator.py` | Validación: ratio ≥60%, monotonic, tolerancia 3s, filtro publicidad, normalización `[mm:ss.xx]`, headers `[ti:]`/`[ar:]` |
| `lrc_cache.py` | Caché local (SHA-256 key: artista+título+duración, TTL 7d) |
| `offset.py` | `apply_offset_to_bundle()` con signo documentado: **positivo = más tarde** |

### 5.2 Actualizaciones

| Archivo | Cambios |
|---------|---------|
| `adapters/lrclib.py` | User-Agent: `OtterRip/7.2.0 (https://github.com/0o0-ct/Script-sh-to-download-music-)`; retry con backoff (3 intentos); distingue 404/429/5xx; búsqueda exacta con `duration` real del audio; ranking por sincronizada > duración > confianza; `instrumental` → bundle instrumental |
| `adapters/multiweb.py` | Devuelve `LyricsBundle` en vez de `LyricsResult`; reutiliza `validate_and_normalise_lrc()` |
| `lyrics.py` | `run_lyrics()` → `LyricsCache`; `_search_cascade()` → convierte `LyricsResult`→`LyricsBundle`; `_select_best_bundle()` → ranking con duración; `select_best()` → wrapper backward-compatible que devuelve `LyricsResult` original |
| `models.py` | Sin cambios (contrato extendido por `LyricsBundle`) |
| `requirements.txt` | Sin cambios (usando `mutagen>=1.47.0`, `requests>=2.31.0`, `aiohttp>=3.9.0` ya incluidos) |

### 5.3 Comandos de derivación

```bash
cd /home/c0o0c/Música/Script-sh-to-download-music-
git checkout -b fix/lyrics-core
# (archivos creados/modify)
git add -A
git commit -m "fix: LyricsBundle, validación LRC, ranking por duración"
```

---

## 6. Decisiones técnicas clave

1. **El offset se aplica directamente a timestamps**, no se confía en `[offset:]`.
   Signo: **positivo = más tarde** (contrario al header LRC). Documentado en
   `offset.py`.
2. **`LyricsBundle` hereda de `LyricsResult` no**: se eligió composición
   (`bundle_from_legacy()`) para evitar romper el contrato existente. Las
   propiedades `title`, `artist`, `duration`, `timed_entries` permiten que los
   viejos tests accedan a datos del bundle.
3. **`LYRICS_UPGRADE_PLAIN`** ahora se mapea a `--upgrade-plain` en el Bash
   (fila 2298) y a `upgrade_plain` en `_process_file` (fila 543). Solo se aplica
   a pistas sin letra ya incrustada.
4. **El ranking en `_select_best_bundle()`**:
   ```
   (0) tiene sincronizada > (1) |duraación - audio|  > (2) confidence
   ```
   No se descarta una letra sincronizada por `|Δduración| > 3s` si `confidence`
   lo soporta; se añade warning y se baja confianza.

---

## 7. Riesgos

| Riesgo | Severidad | Mitigación |
|--------|-----------|------------|
| Cambios en `select_best()` rompen el ordenamiento de proveedores en pipelines externos | Medio | Wrapper `select_best()` mantiene firma original; tests existentes pasan |
| Cache TTL 7d puede servir datos obsoletos tras actualizaciones de proveedores | Bajo | Cache keyed by (artist, title, duration); TTL configurable |
| `_search_cascade` ahora devuelve `LyricsBundle` en el path new, `LyricsResult` en el legacy | Medio | Raja de tipo `isinstance()` detecta entrada; `select_best()` wrapper devuelve `LyricsResult` original |
| `_parse_lrc` en `lrclib.py` re-exportado para tests antiguos | Bajo | Alias a `parse_lrc` desde `lrc_validator` (mesma semántica, ahora con ms) |

---

## 8. Pases siguientes (Parte 2 y 3)

| Parte | Objetivo | Depende de |
|-------|----------|------------|
| **Parte 1** (listo) | `LyricsBundle`, normalización, ranking, código limpio | — |
| **Parte 2** | Escritura de tags/archivos `.lrc`/.txt; integración con `offset` | Bundle `lrc`, `lines` en ms |
| **Parte 3** | Menú Bash, modo palabra a palabra (`kind="word"`), release | `kind` expuesto; `lrc` normalizado |

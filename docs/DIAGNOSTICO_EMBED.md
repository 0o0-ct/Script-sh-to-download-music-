# Diagnóstico: incrustación de letras sincronizadas (Parte 2)

**Fecha:** 2026-11-13 · **Rama:** `fix/lyrics-embed` · **Alcance:** escritura de letras en tags + sidecar `.lrc`, orden del pipeline, verificación y reparación.

> Nota de coordinación: durante esta fase la **Parte 1** (búsqueda/selección) estaba editando
> `lib/otterrip_core/lyrics.py`, `__main__.py`, `adapters/*` y `Down-Linux-music_v7.sh` en el mismo
> *working tree*. Las referencias `archivo:línea` de este documento apuntan al **estado en `HEAD`
> (`dd0bee3`)**, que es la línea base sobre la que se define la Parte 2. Los ficheros nuevos de la
> Parte 2 (`lyrics_embed.py`, `tests/test_lyrics_embed.py`) no colisionan con la Parte 1.

---

## 1. Entorno verificado

| Herramienta | Versión / ruta |
|---|---|
| Python | 3.14.10 (`/usr/bin/python3`) |
| mutagen | 1.48.1 (ya estaba en `requirements.txt`: `mutagen>=1.47.0`) |
| ffmpeg / ffprobe | `/usr/bin/ffmpeg`, `/usr/bin/ffprobe` |
| mid3v2 / metaflac | `/usr/bin/mid3v2`, `/usr/bin/metaflac` (mutagen / ffmpeg) |
| pytest | **no instalado** en el sistema; PEP 668 bloquea `pip install --user` → se usó un venv aislado en `/tmp/opencode/venv` |

No hace falta ninguna dependencia nueva: `mutagen` ya está en `requirements.txt`.

## 2. Mapa de la escritura actual (dónde se escribe hoy)

Búsqueda: `grep -rniE "USLT|SYLT|lyrics|\.lrc|mutagen|embed|-metadata|embed-thumbnail|parse-metadata" Down-Linux-music_v7.sh lib/`

| Punto de escritura | Ubicación | Qué hace |
|---|---|---|
| `_embed_lyrics()` | `lib/otterrip_core/lyrics.py:134-204` | **Escritura real de tags.** Ramas por `isinstance(audio, MP3/FLAC/MP4)`; el resto de formatos cae en el `elif` vacío → solo `audio.save()` |
| `_write_lrc_file()` | `lib/otterrip_core/lyrics.py:207-220` | Sidecar `.lrc` (sync) o `.txt` (plana), modo `file` |
| `_process_file()` | `lib/otterrip_core/lyrics.py:565-582` | Modo `embed`: llama a `_embed_lyrics()` y escribe el `.lrc` con `fpath.with_suffix(".lrc")` |
| `_embed_lyrics_text()` | `lib/otterrip_core/lyrics_tool.py:84-97` | Subcomando `lyrics-tool set`: solo `USLT` plano, **sin `SYLT`**, `lang="eng"` fijo |
| `_get_embedded_lyrics()` | `lib/otterrip_core/lyrics_tool.py:36-54` | Lectura: solo mira `USLT`/`LYRICS`/`©lyr`; **nunca `SYLT`** |
| `_has_lyrics()` | `lib/otterrip_core/lyrics.py:97-112` | Detección: `USLT:`/`SYLT:` (MP3), `LYRICS`/`SYNCEDLYRICS` (FLAC), `©lyr`/átomo iTunes (MP4) |
| `has_lyrics` de biblioteca | `lib/otterrip_core/library.py:44,50,56` | Solo `USLT`/`LYRICS`/`©lyr` → **no ve `SYLT` ni `SYNCEDLYRICS`** |
| Flags de yt-dlp | `Down-Linux-music_v7.sh:3098-3107` | `--embed-metadata`, `--embed-thumbnail`, `--convert-thumbnails jpg` |
| Subtítulos | `Down-Linux-music_v7.sh:3111` | `--write-subs --sub-langs "es,en" --sub-format "lrc/best" --convert-subs lrc` → genera `Título [id].en.lrc` |
| ID3 | `Down-Linux-music_v7.sh:3116-3118` | `--postprocessor-args "ffmpeg:-id3v2_version 3 -write_id3v1 1"` (solo si `AFMT=mp3`) |
| Orden del pipeline | `Down-Linux-music_v7.sh:3192-3233` (`post_process_session`) | `verify-session` → `fix_metadata_batch` (línea 3223) → `process_lyrics` (línea 3227) |
| Plantillas de nombre | `Down-Linux-music_v7.sh:3518-3530` | `song`: `%(title)s [%(id)s].%(ext)s` · `playlist`: `%(playlist_title)s/%(playlist_index)03d - ...` · `album+split`: `%(album,playlist_title)s/%(playlist_index)02d - ...` |
| Conversión local | `lib/otterrip_core/convert.py:24,68` | `ffmpeg -i src -map_metadata 0 -map 0 dst` |
| Reparador | `lib/otterrip_core/repair.py:106-134` | Copia a `backups/` y reescribe tags con mutagen (el SYLT sobrevive: mutagen preserva los frames que parsea) |

## 3. Material de prueba

Generado con el flujo real de ffmpeg (incluye `-id3v2_version 3 -write_id3v1 1` para el MP3, igual
que hace yt-dlp):

```bash
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a libmp3lame -b:a 128k -id3v2_version 3 -write_id3v1 1 test.mp3
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a flac        test.flac
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a libopus     test.opus
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a libvorbis   test.ogg
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a aac -b:a 128k test.m4a
```

Letra de prueba **inventada**: `[00:01.234] Inventada uno` / `[00:05.000] Inventada dos`.

## 4. Volcados de tags (evidencia)

### 4.1 MP3 — `mid3v2 -l` / mutagen

```
SYLT=[1234ms]: Inventada uno
[5000ms]: Inventada dos
TSSE=Lavf63.1.102
USLT==eng=Inventada uno
Inventada dos

[mutagen id3 version] (2, 4, 0)
[mutagen keys] ['TSSE', 'USLT::eng', 'SYLT::eng']
  USLT(encoding=UTF8, lang='eng', desc='', text='Inventada uno\nInventada dos')
  SYLT(encoding=UTF8, lang='eng', format=2, type=1, desc='', text=[('Inventada uno', 1234), ('Inventada dos', 5000)])
```

→ El `SYLT` **sí** se escribe y es correcto (`format=2` = milisegundos, `type=1` = letra).
→ El `USLT` lleva **texto plano**: es el frame que leen la mayoría de reproductores, y no sincroniza.

### 4.2 FLAC — `metaflac --export-tags-to=-`

```
encoder=Lavf63.1.102
LYRICS=[00:01.234] Inventada uno
LYRICS=[00:05.000] Inventada dos
SYNCEDLYRICS=[00:01.234] Inventada uno
[00:05.000] Inventada dos
```

→ No existe `UNSYNCEDLYRICS` (texto plano) → el reproductor que solo lee `LYRICS` ve los timestamps
como texto literal, y el que solo lee `UNSYNCEDLYRICS` no ve nada.

### 4.3 OPUS / OGG — `ffprobe -show_entries stream_tags` / mutagen

```
[mutagen type] OggOpus
[mutagen keys] ['encoder']
  TAG 'encoder' = ['Lavc63.1.102 libopus']
```

→ **No se escribe absolutamente nada.** `_embed_lyrics()` (`lyrics.py:149-204`) solo tiene ramas
`isinstance` para `MP3`, `FLAC` y `MP4`; un `OggOpus` cae fuera y solo se ejecuta `audio.save()`.

### 4.4 M4A — `ffprobe -show_entries format_tags`

```
TAG:lyrics=[00:01.234] Inventada uno
[00:05.000] Inventada dos;[00:01.234] Inventada uno
[00:05.000] Inventada dos

[mutagen keys] ['©too', '----:com.apple.iTunes:LYRICS', '©lyr']
  '©lyr' = ['[00:01.234] Inventada uno\n[00:05.000] Inventada dos']
  '----:com.apple.iTunes:LYRICS' = [MP4FreeForm(b'[00:01.234] Inventada uno\n...', UTF8)]
```

→ Correcto: `©lyr` (estándar) + átomo libre de iTunes, ambos con el LRC.

### 4.5 Pérdida por `ffmpeg -map_metadata 0` (camino de conversión local)

```
C) ffmpeg -map_metadata 0 sobre MP3 con SYLT/USLT
   USLT=0 SYLT=0 version=(2, 4, 0)
     TXXX:lyrics-eng = 'Inventada uno\nInventada dos'      <-- USLT reescrito como TXXX
   ffprobe: TAG:lyrics-eng=Inventada uno\nInventada dos
```

→ En MP3, `ffmpeg` **destruye el `SYLT`** y convierte el `USLT` en un `TXXX:lyrics-eng`.
→ En FLAC y M4A `-map_metadata 0` **conserva** `LYRICS`/`SYNCEDLYRICS` y `©lyr`.

### 4.6 `delall("USLT:")` no borra todo (mutagen 1.48)

```python
t.add(USLT(encoding=3, lang="eng", desc="",    text="hola"))
t.add(USLT(encoding=3, lang="spa", desc="",    text="hola-es"))
t.add(USLT(encoding=3, lang="eng", desc="otro", text="hola-otro"))
t.add(SYLT(encoding=3, lang="eng", desc="", format=2, type=1, text=[("a", 100)]))

HashKeys: ['USLT::eng', 'USLT::spa', 'USLT:otro:eng', 'SYLT::eng']
tras delall('USLT:') quedan: ['USLT:otro:eng', 'SYLT::eng']      <-- sobrevive el de desc="otro"
```

`HashKey` de mutagen es `"{FrameID}:{desc}:{lang}"` (`mutagen/id3/_frames.py`), y `delall(key)`
(`mutagen/id3/_tags.py:265`) hace `key + ":"` → `delall("USLT:")` solo barre los frames con `desc`
vacío. **La llamada correcta es `delall("USLT")`.**

### 4.7 mutagen e ID3v2.3

```python
USLT._to_v23 ERROR: AttributeError("'USLT' object has no attribute '_to_v23'")
SYLT._to_v23 ERROR: AttributeError("'SYLT' object has no attribute '_to_v23'")
```

`ID3.save(..., v2_version=4)` es el default (`mutagen/id3/_file.py:224`), así que **mutagen siempre
reescribe el MP3 como ID3v2.4** (confirmado: `(2, 4, 0)` tras guardar un fichero creado con
`-id3v2_version 3`), invalidando en silencio el flag de yt-dlp.

## 5. Veredicto de las hipótesis

| # | Hipótesis | Veredicto | Evidencia |
|---|---|---|---|
| **H1** | La letra va solo como texto plano, o los timestamps se eliminan al escribir; en MP3 no existe SYLT | **Parcialmente CONFIRMADA** | El `SYLT` del MP3 **sí existe y es correcto** (§4.1) → la parte "no existe SYLT" es **FALSA**. Pero **CONFIRMADA** en: (a) el `USLT` del MP3 (frame que más reproductores leen) es texto plano; (b) **OGG/Opus no recibe nada** (§4.3); (c) FLAC no tiene `UNSYNCEDLYRICS` y el `LYRICS` va con timestamps embebidos; (d) `lyrics-tool set` sólo escribe `USLT` plano, sin `SYLT` (`lyrics_tool.py:84-97`) |
| **H2** | La letra se escribe antes de `--embed-metadata`/`--embed-thumbnail`/conversión, que la borran | **DESCARTADA para el flujo principal / CONFIRMADA para conversión local** | Flujo real: `post_process_session` (`Down-Linux-music_v7.sh:3192-3233`) = verify → `fix_metadata_batch` (3223) → `process_lyrics` (3227). Las letras se escriben **al final**, después de yt-dlp y de la carátula. **Pero** `py_core convert` (`convert.py:24`) usa `-map_metadata 0`, que **destruye el `SYLT` del MP3** (§4.5) |
| **H3** | Quedan USLT/campos antiguos (planos) o varios frames con distinto `lang`/`desc`, y el reproductor lee el equivocado | **CONFIRMADA** | `delall("USLT:")` deja vivos los `USLT` con `desc` no vacío (§4.6) → se acumulan frames duplicados/variantes. Además `lang="eng"` **fijos** (`lyrics.py:154,165`) irrespective de `LYRICS_LANG`, y `lyrics-tool show/compare` (`lyrics_tool.py:36-54`) **sólo lee `USLT`**, es decir, lee justo el frame plano que puede ser el equivocado |
| **H4** | Versión/codificación ID3 incoherente (UTF-8 en ID3v2.3), saltos `\r\n`, o `lang` inválido | **CONFIRMADA (parcial)** | No hay UTF-8 en v2.3 porque mutagen **sube** el fichero a ID3v2.4 (§4.7) → coherente, pero **invalida `-id3v2_version 3`** de `Down-Linux-music_v7.sh:3117`. `lang` inválido: `lang="eng"` hardcodeado para cualquier idioma. `USLT`/`SYLT` **no son convertibles a ID3v2.3** en mutagen (no tienen `_to_v23`) → si se fuerza v2.3 habría que usar `encoding=1` (UTF-16) |
| **H5** | El `.lrc` no existe, o su nombre no coincide con el audio final, o hay BOM/codificación rara | **DESCARTADA en el flujo principal / CONFIRMADA en casos concretos** | El sidecar se escribe con `fpath.with_suffix(".lrc")` (`lyrics.py:570`) sobre la ruta **final** del manifiesto de sesión, y las plantillas de yt-dlp (`Down-Linux-music_v7.sh:3518-3530`) ya están aplicadas en ese momento → nombre correcto. **Pero**: (a) el sidecar sólo se escribe **si hay sync** (`lyrics.py:568`) → en letra plana no hay ningún `.lrc` ni `.txt`; (b) `yt-dlp --write-subs --convert-subs lrc` deja `Título [id].en.lrc` **huérfano** junto a nuestro `Título [id].lrc`; (c) `Path.write_text` usa `newline=None` → en Windows escribiría `\r\n` (en Linux `\n`, sin BOM: sin BOM confirmado) |
| **H6** | Errores de escritura silenciados (`|| true`, `except: pass`) | **CONFIRMADA** | `lyrics.py:203-204` (`except Exception: return False`), `lyrics.py:218-219`, `lyrics.py:576-577` (sidecar), `lyrics_tool.py:96-97`, `fix_metadata.py:83/126/146` (`except Exception: pass`), y en Bash `fix_metadata_batch ... || true` (3223) y `process_lyrics ... || true` (3227). Un fallo de escritura se convierte en "letra no encontrada" o en un `✔` engañoso: **no hay verificación posterior en disco** |

## 6. Tabla formato → dónde queda hoy la letra

| Formato | Campo sincronizado | Campo plano | Sidecar `.lrc` | Estado |
|---|---|---|---|---|
| MP3 | `SYLT` (`format=2`, `type=1`, `lang=eng`) | `USLT` (`lang=eng`) | sí (si hay sync) | **Parcial** — el `USLT` plano es lo que lee la mayoría |
| FLAC | `SYNCEDLYRICS` = LRC | — (`LYRICS` = LRC con timestamps) | sí (si hay sync) | **Deficiente** — falta `UNSYNCEDLYRICS`; `LYRICS` mezcla texto y marcas |
| OGG (Vorbis) | **ninguno** | **ninguno** | sí (si hay sync) | **Roto** |
| Opus | **ninguno** | **ninguno** | sí (si hay sync) | **Roto** |
| M4A/MP4 | `----:com.apple.iTunes:LYRICS` (LRC) | — (`©lyr` = LRC con timestamps) | sí (si hay sync) | **Parcial** — falta texto plano en `©lyr` |

## 7. Defectos a corregir en la Parte 2

| ID | Defecto | Severidad |
|---|---|---|
| **D1** | OGG y Opus no reciben letra alguna (rama `isinstance` inexistente) | **Crítico** |
| **D2** | `USLT`/`SYLT` con `lang="eng"` fijo, ignorando `LYRICS_LANG` | Alto |
| **D3** | `delall("USLT:")` no borra los `USLT` con `desc` no vacío → frames duplicados | Alto |
| **D4** | No hay verificación posterior a la escritura: se marca `✔` sin releer el fichero | Alto |
| **D5** | `LYRICS` (FLAC) y `©lyr` (M4A) llevan el LRC con timestamps en vez de texto plano → se ve como ruido en reproductores sin LRC | Alto |
| **D6** | Falta `UNSYNCEDLYRICS` en FLAC/OGG/Opus | Medio |
| **D7** | `lyrics-tool show/compare` y `library report` sólo detectan `USLT`/`LYRICS`/`©lyr` → no ven `SYLT` ni `SYNCEDLYRICS` | Medio |
| **D8** | `lyrics-tool set` incrusta solo texto plano (pierde la sincronización) | Medio |
| **D9** | Errores de escritura silenciados; un `FAIL` no se distingue de un `✔` | Medio |
| **D10** | El ID3v2.3 pedido por `-id3v2_version 3` se convierte silenciosamente a v2.4 | Bajo (documentar) |
| **D11** | Sidecars huérfanos `*.en.lrc` de `--write-subs` | Bajo |
| **D12** | Sin copia de seguridad ni escritura atómica al reescribir tags | Alto |

## 8. Requisitos derivados para la Parte 2

1. Escritura **con mutagen** (nunca `-metadata` de ffmpeg) y **por formato**: MP3 (`USLT` + `SYLT`,
   con `lang` ISO 639-2 derivado de `LYRICS_LANG`, `XXX` si se desconoce), FLAC/OGG/Opus
   (`LYRICS` = LRC, `UNSYNCEDLYRICS` = texto plano), M4A (`©lyr` + átomo iTunes).
2. `LYRICS_EMBED_STYLE` = `lrc` (por defecto) | `plain`: decide si el campo de letras lleva el LRC
   con timestamps o texto limpio. **Compensación documentada**: con `lrc`, los reproductores que no
   entienden LRC muestran `[00:12.34]` como texto; con `plain`, la sincronización sólo queda en
   `SYLT`/`.lrc`.
3. Sidecar `.lrc` en UTF-8 **sin BOM** y `\n`, con el **mismo nombre base que el audio final**,
   escrito **después** de cualquier renombrado; si el audio se renombra/mueve después, el sidecar
   se renombra con él.
4. La letra es el **último** paso que toca tags (después de yt-dlp, conversión, carátula y renombrado).
5. Escritura **segura**: copia de respaldo → escribir → **releer del disco y verificar** → borrar
   respaldo; si falla, restaurar. Conservar el resto de tags (carátula, ReplayGain). **Idempotente**.
6. Verificación obligatoria con resultado por pista **OK / WARN / FAIL** reflejado en el resumen de
   sesión; un FAIL deja la pista marcada como **"necesita reparación"**, no como éxito.
7. Subcomandos `lyrics verify <dir>` (solo lectura) y `lyrics repair <dir>` (reincrusta desde el
   sidecar; si no existe, vuelve a pedir la letra; renombra sidecars huérfanos; `--dry-run`).

## 9. Anexo — cómo reproducir

```bash
# 1) material de prueba
ffmpeg -f lavfi -i sine=frequency=440:duration=3 -c:a libmp3lame -b:a 128k \
       -id3v2_version 3 -write_id3v1 1 test.mp3
# 2) incrustar con la implementación actual
PYTHONPATH=lib python3 -m otterrip_core lyrics . mp3 --providers lrclib   # o el helper directo
# 3) volcar
mid3v2 -l test.mp3          # MP3
metaflac --export-tags-to=- test.flac
ffprobe -hide_banner -show_entries format_tags test.opus
ffprobe -hide_banner -show_entries format_tags test.m4a
```

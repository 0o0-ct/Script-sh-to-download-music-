# Changelog

## [Sin publicar] - 2026-11-17

### Fixed

- **`clean_filenames()` nunca se ejecutaba (fallo silencioso).** El heredoc
  de Python embebido en Bash no recibía argumentos (`python3 -` deja
  `sys.argv = ['-']`, por lo que `sys.argv[1]` lanzaba `IndexError`, y
  `__file__` tampoco está definido al leer código de stdin). El error se
  tragaba con `2>/dev/null`, así que los nombres de archivo nunca se
  limpiaban. Ahora la limpieza es un subcomando del backend
  (`py_core clean-names`) en el módulo nuevo
  `lib/otterrip_core/clean_names.py`, con argumentos reales y sin `jq`.
- **El renombrado rompía el paso de letras.** `clean_filenames` renombraba
  los archivos DESPUÉS de que `verify-session` generara el manifiesto
  `session.verified`, así que el motor de letras dejaba de encontrar las
  pistas. Ahora el renombrado reescribe también `session.final` y
  `session.verified` (claves `filepath` / `file`), y se ejecuta tras la
  verificación (que localiza pistas por el ID de YouTube en el nombre) y
  antes de las letras.
- **La limpieza ahora cubre subtítulos compuestos y carpetas anidadas:**
  `.en.lrc`, `.es.vtt`, `.es-419.lrc`, portadas `.jpg`/`.webp`/`.png`,
  y archivos dentro de subcarpetas de playlist/álbum. Los archivos ya
  limpios no se tocan y las colisiones se descartan sin sobrescribir.
- **"Restablecer valores predeterminados" (opción 19) dejaba variables sin
  restaurar en memoria** si el listado del esquema y el código de la
  función se desalineaban; ahora itera `CONFIG_KEYS` usando `CONFIG_SCHEMA`
  como única fuente de verdad, garantiza explícitamente
  `FETCH_LYRICS=true`, `LYRICS_MODE=embed`, `LYRICS_PREFER_SYNCED=true`,
  `LYRICS_SYNC_LEVEL=line`, `LYRICS_EMBED_STYLE=lrc`, y limpia
  `OTTERRIP_PROXY` vacío (antes `config_apply_value` no lo borraba).
- **El ajuste manual de desfase (Gestor avanzado → opción 4) no corregía
  la letra incrustada.** Llamaba a `lyrics-tool shift`, que solo reescribe
  el archivo `.lrc` sidecar; lo que se escucha en el reproductor es el tag,
  así que el desfase seguía igual. Ahora usa `lyrics-manage shift`, que
  actualiza SYLT + `TXXX:LYRICS` + sidecar (verificado con un MP3 real).
- **La auto-alineación no podía leer M4A/AAC.** librosa lee el audio vía
  libsndfile (`soundfile`), que no soporta AAC — el formato principal de
  OtterRip — y el fallback `audioread` no está instalado, así que cada
  pista m4a soltaba «no se pudo leer el audio» y la corrección nunca
  actuaba. Ahora `align.py` decodifica con ffmpeg (requisito del script)
  a PCM mono 22050 Hz por stdout cuando libsndfile falla. Verificado con
  los m4a reales del usuario (88 pistas) y con test de regresión
  (`TestOnsetEnvelopeM4a`).
- **La nota de omisión de la auto-alineación se repetía una vez por pista
  (86 líneas idénticas en una playlist real).** El texto interpolaba el
  desfase medido, así que no era deduplicable. Ahora las notas son
  constantes estables (`NOTE_NO_LIBROSA`, `NOTE_AUDIO_FAIL`,
  `NOTE_NO_SIGNIFICANT`, `NOTE_UNRELIABLE`, `NOTE_EXCEEDS_DURATION`): la
  primera ocurrencia se imprime, todas se cuentan, y al final aparece un
  bloque «Auto-corrección de letras (resumen)» con el conteo por nota.

### Added

- **Alineación automática de letras con el audio (`LYRICS_AUTO_ALIGN=true`
  por defecto).** Las letras de LRCLIB/Multi-Web están sincronizadas para
  una grabación concreta de la canción; cuando el audio descargado es otra
  edición (radio edit, intro distinta, versión en vivo), la letra completa
  va adelantada o atrasada de forma constante. El módulo nuevo
  `lib/otterrip_core/align.py` estima UN desfase constante correlacionando
  los inicios de línea con la envolvente de onsets del audio (librosa,
  dependencia opcional) y aplica el cambio solo si es consistente:
  confianza ≥ 0.55 sobre la tasa de aciertos por línea, |desfase| entre
  200 ms y ±15 s, y nunca más allá de la duración del audio (+3 s). Sin
  librosa la alineación se omite con un aviso y no se toca nada.
- **Opciones 16 y 17 en Configuración de letras:** activar/desactivar la
  alineación automática e instalar librosa en el venv aislado. El estado
  (activa, activa sin librosa, desactivada) se muestra en el menú y se
  cachea junto al resto de dependencias. Con un backend antiguo
  (instalación script-only) el flag `--auto-align` no se envía, para no
  romper la búsqueda de letras con un argumento desconocido.
- **Configuración de letras reescrita en lenguaje llano.** El menú mezclaba
  jerga técnica (USLT/SYLT, sidecar, API key, syncedlyrics) y tres
  conceptos casi homónimos («Sincronizadas», «Nivel sync»,
  «Auto-alineación»). Ahora se agrupa en «Búsqueda / Dónde se guardan /
  Sincronización con la música», con la explicación a la derecha de cada
  valor; los conceptos se renombran (Tipo preferido, Precisión,
  Auto-corrección) y la opción 14 describe lo que realmente hace
  (descarta las letras de baja calidad, no solo advierte). Nuevo helper
  `pad_cols()`: el `printf %-Ns` de Bash cuenta bytes y no caracteres, lo
  que desalineaba las etiquetas con tildes («Precisión», «corrección»).

### Notas

- **`HTTP Error 403: Forbidden` de YouTube es externo al script.** Lo causa
  el bloqueo anti-bot de YouTube contra yt-dlp (IP limitada, sin cookies,
  versión de yt-dlp desactualizada). Mitigaciones desde el propio script:
  menú 16 (cookies de navegador o archivo `cookies.txt`), menú 18 (proxy),
  y actualizar yt-dlp (`yt-dlp -U` o el gestor de paquetes). El script ya
  reintenta errores recuperables automáticamente una vez por descarga.

## [7.3.4] - 2026-11-13

### Fixed

- **YouTube Music repeated artist prefix broke lyrics search.** Titles like
  "JON CARLO - Jon Carlo - Quema Mi Vida" were not cleaned, so LRCLIB could
  never match the track. `clean_title()` now strips the repeated artist
  prefix, and `build_query_terms()` extracts the artist name automatically
  when the metadata artist field is empty. This dramatically improves match
  rates for YouTube Music playlists where the title embeds the artist twice.
- **Trailing noise "(Audio Oficial)" and "- Musica Catolica" prevented matches.**
  Added regex patterns for `(Audio Oficial)`, `(Official Audio)`, and trailing
  genre suffixes like `- Musica Catolica` / `- Catholic Music`.

## [7.3.3] - 2026-11-13

### Fixed

- **Multi-Web fallback was skipped when LRCLIB returned plain lyrics.** The cascade
  in `_search_cascade()` returned immediately after the first provider found any
  result, so Multi-Web was never consulted when LRCLIB returned plain (non-synced)
  lyrics. Now all providers are queried, and a synced result from a later provider
  upgrades a plain result from an earlier one. This increases the chance of finding
  synchronized lyrics even when LRCLIB only has plain text.

## [7.3.2] - 2026-11-13

### Fixed

- **Lyrics search failed for many tracks with multi-artist metadata.** YouTube Music
  embeds all artists comma-separated (e.g. "Un Corazón, Kim Richards, Marco Barrientos"),
  causing LRCLIB lookups to return zero results. `clean_artist()` now keeps only the
  first artist for the search query, dramatically improving match rates.
- **Title noise from YouTube Music prevented matches.** Titles like
  "Forofos ｜ Hakuna Group Music", "DESCALZOS (Letra) ｜ Hakuna ｜ Música Católica",
  "Jesucristo Basta (En Vivo desde el Auditorio Nacional)", and
  "Tú en Mí, Yo en Ti (Versión Comunión)" are now cleaned to just the track name.
  Added regex patterns for `│` (fullwidth pipe), `(Letra)`, `(En Vivo)`,
  `(Acústico)`, and `(Versión ...)` in Spanish.
- **LRCLIB fallback: title-only search.** When artist+title search returns nothing,
  the adapter now retries with title-only to find tracks that LRCLIB stores under
  a different artist name.

## [7.3.1] - 2026-11-13

### Fixed

- **Critical: lyrics embedding failed silently on all formats.** `to_legacy_result()`
  in `lyrics_bundle.py` was missing the `has_sync` field, causing an
  `AttributeError` when `_embed_lyrics()` tried to check `result.has_sync`.
  The exception was silently swallowed, so every embed returned "error al
  incrustar" even though the lyrics were found. Added `has_sync` to the
  legacy dict and added error logging in `_embed_lyrics()` for future debugging.

## [7.3.0] - 2026-11-13

### Added

- `lyrics verify <dir>`: read-only audit of library lyrics. Classifies each
  track as OK / WARN / FAIL (no lyrics, broken timestamps, empty tag, orphan
  sidecar). Reports per-track + totals. Exit code != 0 if any FAIL.
- `lyrics repair <dir>`: re-embeds lyrics from sidecar .lrc into audio tags;
  `--dry-run` mandatory before applying. Backup .bak + atomic write (temp +
  rename). Post-write re-read and re-validation (OK/WARN/FAIL per track).
- `lyrics shift <file> <+/-ms>`: adjusts offset on both embedded tag AND
  sidecar .lrc. Reuses `offset.py`. Post-write verification.
- Library summary counters: synced lyrics (SYLT/LRC), plain lyrics (USLT
  without timestamps), and no lyrics — shown in verify/repair reports.
- Enhanced LRC module (`enhanced_lrc.py`): parser/serializer for word-level
  timestamps `<mm:ss.xxx>`. Conversion from/to standard LRC. Validation with
  monotonicity and tolerance checks.
- Word-level alignment stub (`align_words_stub`) with graceful fallback when
  `stable-ts` / `librosa` are not installed.
- `requirements-align.txt`: separate heavy dependencies for opt-in word-level
  alignment.
- Bash menu integration: Advanced Lyrics Manager items 7/8 now invoke
  `lyrics-manage verify` / `lyrics-manage repair` with `--dry-run` preview
  and `--lang` derived from `LYRICS_LANG`.
- GitHub Actions CI workflow: pytest + shellcheck on push/PR.

### Changed

- Backend tar rebuilt from working tree: `query_clean.py` now includes the
  `(feat.)` parenthetical fix. `BACKEND_SHA256` updated.
- `REQUIRED_FILES` updated with `lyrics_verify.py` and `enhanced_lrc.py`.
- Language ISO-3 code (`_lang_iso3`) derived from `LYRICS_LANG` instead of
  hardcoded `"eng"`.
- Test suite expanded: 101 tests (was 68), covering Enhanced LRC (≥10 edge
  cases), verify, repair, shift, and lrc_validator.

### Fixed

- Backend archive SHA-256 mismatch: distributed tar no longer matches working
  tree `query_clean.py`. Rebuilt and hash updated.
- Menu 7/8 in Advanced Lyrics Manager now use dedicated lyrics commands
  instead of generic `library` / `repair`.
- LRC timestamp format: changed from `[mm:ss.xxx]` (3 digits) to
  `[mm:ss.xx]` (2 digits centiseconds) for BlackPlayer, PowerAmp, and
  Samsung Music compatibility.
- `split_feat()` now extracts featured artists even when trailing noise
  (e.g. `[Lyrics]`) follows the `(feat. ...)` parenthetical — improves
  search accuracy for songs with featured artists.
- Sidecar `.lrc` file is now generated for plain lyrics too (when
  `LYRICS_SIDECAR=true`), not only for synced lyrics — improves
  compatibility with players that only read sidecar files.
- `pyproject.toml` added so `pytest` runs without manual `PYTHONPATH`.

## [7.2.1] - 2026-11-13

### Added

- New lyrics configuration variables: `LYRICS_SYNC_LEVEL` (line/word/auto),
  `LYRICS_SIDECAR`, `LYRICS_EMBED_STYLE`, `LYRICS_MIN_CONFIDENCE`,
  `LYRICS_OFFSET_MS` — all persisted in `$XDG_CONFIG_HOME/otterrip/config`.
- Menu options 12–15 in the Lyrics settings menu to configure sync level,
  sidecar generation, minimum confidence threshold, and global offset.
- Advanced Lyrics Manager options 7 (library verify) and 8 (library repair)
  with dry-run preview before applying changes.
- Backend Python accepts `--sync-level`, `--sidecar`, `--embed-style`,
  `--min-confidence`, and `--offset-ms` CLI arguments.
- Enhanced LRC (`.elrc`) sidecar file generation for word-level lyrics.
- Confidence gate: lyrics below `LYRICS_MIN_CONFIDENCE` are skipped and
  logged as warnings instead of overwriting better existing lyrics.
- Global offset applied to all lyrics timestamps before embedding.

### Changed

- `LyricsBundle` now carries `title`, `artist`, and `album` fields from
  `bundle_from_legacy()` for correct title/artist matching during selection.
- Fixed `UnboundLocalError` in `_search_cascade` logger call.
- Fixed Bash arithmetic error in `lyrics_common_args` confidence conversion.

### Fixed

- Test suite updated for `LyricsBundle` API (39 pytest, 83 bats).
- ShellCheck clean (no new warnings/errors).

## [7.2.0] - 2026-11-12

### Added

- Sequential lyrics fallback across LRCLIB, Musixmatch, NetEase, Megalobiz, and
  Genius using the supported `syncedlyrics` search API.
- Concurrent per-track lyrics lookup and embedding, limited by the configured
  worker count.
- Lyrics dependencies installed with the isolated OtterRip environment.

### Changed

- Reject empty, low-confidence, or mismatched lyrics instead of accepting a
  poor fallback result.
- Allow configuring up to 32 download and lyrics workers.

## [7.1.0] - 2026-11-12

### Added

- Automatic, version-pinned Python backend download for script-only installs.
- SHA-256 verification, package path checks, staged installation, and startup
  validation for the backend.
- Versioned backend package under the user's XDG data directory, with offline
  reuse when a compatible backend is already installed.

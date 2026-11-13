# Changelog

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

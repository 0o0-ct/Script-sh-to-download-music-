# Changelog

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

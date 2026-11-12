# Changelog

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

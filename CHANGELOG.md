# Changelog

## [7.1.0] - Unreleased

### Added

- Automatic, version-pinned Python backend download for script-only installs.
- SHA-256 verification, package path checks, staged installation, and startup
  validation for the backend.
- Versioned backend package under the user's XDG data directory, with offline
  reuse when a compatible backend is already installed.

#!/bin/bash
# build_backend.sh — Build OtterRip backend TAR package with SHA-256
#
# Usage: ./build_backend.sh
#
# This script:
# 1. Reads the version from lib/otterrip_core/VERSION
# 2. Updates REQUIRED_FILES with all .py modules
# 3. Creates packages/otterrip-backend-v{version}.tar.gz
# 4. Calculates SHA-256 and updates Down-Linux-music_v7.sh
# 5. Verifies TAR integrity

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CORE_DIR="$SCRIPT_DIR/lib/otterrip_core"
VERSION_FILE="$CORE_DIR/VERSION"
REQUIRED_FILES="$CORE_DIR/REQUIRED_FILES"
PACKAGES_DIR="$SCRIPT_DIR/packages"
BASH_SCRIPT="$SCRIPT_DIR/Down-Linux-music_v7.sh"

# Read version
if [[ ! -f "$VERSION_FILE" ]]; then
    echo "ERROR: VERSION file not found at $VERSION_FILE"
    exit 1
fi
VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
echo "Building OtterRip backend v${VERSION}..."

# Update REQUIRED_FILES with all .py files
echo "Updating REQUIRED_FILES..."
{
    echo "__init__.py"
    echo "__main__.py"
    echo "adapters/__init__.py"
    echo "adapters/base.py"
    echo "adapters/lrclib.py"
    echo "adapters/multiweb.py"
    echo "adapters/syncedlyrics_adapter.py"
    # List all .py files in core dir, sorted
    find "$CORE_DIR" -maxdepth 1 -name '*.py' -not -name '__init__.py' -not -name '__main__.py' -printf '%f\n' | sort
    # List all .py files in adapters dir
    if [[ -d "$CORE_DIR/adapters" ]]; then
        find "$CORE_DIR/adapters" -maxdepth 1 -name '*.py' -printf 'adapters/%f\n' | sort
    fi
} > "$REQUIRED_FILES"

echo "REQUIRED_FILES updated with $(wc -l < "$REQUIRED_FILES") files."

# Create packages directory
mkdir -p "$PACKAGES_DIR"

# Create TAR archive
TAR_NAME="otterrip-backend-v${VERSION}.tar.gz"
TAR_PATH="$PACKAGES_DIR/$TAR_NAME"

echo "Creating $TAR_NAME..."
tar -czf "$TAR_PATH" --exclude='__pycache__' -C "$SCRIPT_DIR" lib requirements.txt

# Calculate SHA-256
SHA256=$(sha256sum "$TAR_PATH" | awk '{print $1}')
echo "SHA-256: $SHA256"

# Update bash script with new SHA-256 and version
if [[ -f "$BASH_SCRIPT" ]]; then
    echo "Updating $BASH_SCRIPT..."
    # Update OTTERRIP_VERSION
    sed -i "s/^OTTERRIP_VERSION=.*/OTTERRIP_VERSION=\"${VERSION}\"/" "$BASH_SCRIPT"
    # Update BACKEND_SHA256
    if grep -q '^BACKEND_SHA256=' "$BASH_SCRIPT"; then
        sed -i "s/^BACKEND_SHA256=.*/BACKEND_SHA256=\"${SHA256}\"/" "$BASH_SCRIPT"
    else
        # Add BACKEND_SHA256 after OTTERRIP_VERSION
        sed -i "/^OTTERRIP_VERSION=/a BACKEND_SHA256=\"${SHA256}\"" "$BASH_SCRIPT"
    fi
    echo "Bash script updated."
else
    echo "WARNING: $BASH_SCRIPT not found, skipping update."
fi

# Verify TAR integrity
echo "Verifying TAR integrity..."
tar -tzf "$TAR_PATH" > /dev/null
echo "TAR integrity verified."

# Generate release notes from git log (if git is available)
if command -v git &> /dev/null; then
    echo ""
    echo "=== Release Notes for v${VERSION} ==="
    cd "$SCRIPT_DIR"
    # Try to find the previous tag
    PREV_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    if [[ -n "$PREV_TAG" && "$PREV_TAG" != "v${VERSION}" ]]; then
        git log "${PREV_TAG}..HEAD" --oneline --no-merges 2>/dev/null || echo "No changes found."
    else
        git log --oneline -20 2>/dev/null || echo "Git log unavailable."
    fi
fi

echo ""
echo "Build complete: $TAR_PATH"
echo "SHA-256: $SHA256"
echo "Version: $VERSION"

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]


class TestBackendPackage(unittest.TestCase):
    def test_pinned_package_matches_script_and_contains_expected_files(self):
        script = (ROOT / "Down-Linux-music_v7.sh").read_text(encoding="utf-8")
        version = re.search(r'^OTTERRIP_VERSION="([^"]+)"$', script, re.MULTILINE)
        digest = re.search(r'^BACKEND_SHA256="([a-f0-9]{64})"$', script, re.MULTILINE)
        self.assertIsNotNone(version)
        self.assertIsNotNone(digest)
        assert version is not None
        assert digest is not None

        package = ROOT / "packages" / f"otterrip-backend-v{version.group(1)}.tar.gz"
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), digest.group(1))

        with tarfile.open(package, "r:gz") as archive:
            members = archive.getmembers()
            names = {member.name for member in members}
            self.assertIn("requirements.txt", names)
            self.assertIn("lib/otterrip_core/__main__.py", names)
            self.assertIn("lib/otterrip_core/VERSION", names)
            self.assertIn("lib/otterrip_core/REQUIRED_FILES", names)
            for member in members:
                path = PurePosixPath(member.name)
                self.assertFalse(path.is_absolute())
                self.assertNotIn("..", path.parts)
                self.assertFalse(member.issym())
                self.assertFalse(member.islnk())

            version_file = archive.extractfile("lib/otterrip_core/VERSION")
            self.assertIsNotNone(version_file)
            assert version_file is not None
            self.assertEqual(version_file.read().decode().strip(), version.group(1))
            manifest_file = archive.extractfile("lib/otterrip_core/REQUIRED_FILES")
            self.assertIsNotNone(manifest_file)
            assert manifest_file is not None
            manifest = {
                f"lib/otterrip_core/{line}"
                for line in manifest_file.read().decode().splitlines()
                if line
            }
            python_modules = {
                name
                for name in names
                if name.startswith("lib/otterrip_core/") and name.endswith(".py")
            }
            self.assertEqual(manifest, python_modules)

    def test_script_only_bootstrap_verifies_then_reuses_backend_offline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            script_dir = temp / "launcher"
            home = temp / "home"
            script_dir.mkdir()
            home.mkdir()
            script = script_dir / "Down-Linux-music_v7.sh"
            shutil.copy2(ROOT / script.name, script)

            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    "OTTERRIP_DATA_DIR": str(temp / "data"),
                    "OTTERRIP_CONFIG_DIR": str(temp / "config"),
                    "OTTERRIP_STATE_DIR": str(temp / "state"),
                    "OTTERRIP_CACHE_DIR": str(temp / "cache"),
                    "TEST_BACKEND_ARCHIVE": str(
                        ROOT / "packages" / "otterrip-backend-v7.1.0.tar.gz"
                    ),
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    """source "$1"
expected_hash="$BACKEND_SHA256"
BACKEND_SHA256="$(printf '%064d' 0)"
if ensure_backend; then exit 10; fi
[[ ! -e "$APP_INSTALL_DIR" ]]
BACKEND_SHA256="$expected_hash"
download_backend_archive() { cp "$TEST_BACKEND_ARCHIVE" "$1"; }
ensure_backend
[[ "$LIB_DIR" == "$APP_INSTALL_DIR/lib" ]]
mv "$APP_INSTALL_DIR/lib/otterrip_core/models.py" "$APP_INSTALL_DIR/lib/otterrip_core/models.py.missing"
if backend_is_valid "$APP_INSTALL_DIR"; then exit 11; fi
mv "$APP_INSTALL_DIR/lib/otterrip_core/models.py.missing" "$APP_INSTALL_DIR/lib/otterrip_core/models.py"
download_backend_archive() { return 73; }
ensure_backend
backend_is_valid "$APP_INSTALL_DIR"
""",
                    "bootstrap-test",
                    str(script),
                ],
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

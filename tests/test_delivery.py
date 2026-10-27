import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeliveryTests(unittest.TestCase):
    def test_git_ignores_staging_and_backup_proxy_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copy(ROOT / ".gitignore", path / ".gitignore")
            subprocess.run(["git", "init", "-q"], cwd=path, check=True)
            for name in ("config/proxy.txt", "config/proxy.next", "config/proxy.txt.bak"):
                result = subprocess.run(["git", "check-ignore", "-q", name], cwd=path)
                self.assertEqual(result.returncode, 0, name)

    def test_parallel_deploy_waits_for_build_and_check_before_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            script = path / "compose.sh"
            script.write_text(
                'case "$1" in\n'
                'build) sleep 0.2; touch built ;;\n'
                'run) test -e built || exit 3; touch checked ;;\n'
                'up) test -e checked || exit 4; touch started ;;\n'
                'esac\n'
            )
            result = subprocess.run(
                ["make", "-j3", "-f", str(ROOT / "Makefile"), "deploy", f"COMPOSE=sh {script}"],
                cwd=path, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue((path / "started").exists())

    def test_init_preserves_existing_client_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "config").mkdir()
            (path / ".env").write_text("CLIENT_CONFIG=kept\n")
            (path / "config/proxy.txt").write_text("CLIENT_SECRET\n")
            subprocess.run(["make", "-f", str(ROOT / "Makefile"), "init"], cwd=path, check=True, capture_output=True)
            self.assertEqual((path / ".env").read_text(), "CLIENT_CONFIG=kept\n")
            self.assertEqual((path / "config/proxy.txt").read_text(), "CLIENT_SECRET\n")

    def test_archive_excludes_live_configuration_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source"
            shutil.copytree(ROOT, path, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", ".env", "proxy.txt"))
            (path / ".env").write_text("PRIVATE_ENV\n")
            (path / "config/proxy.txt").write_text("PRIVATE_PROXY\n")
            (path / "proxy_manager/__pycache__").mkdir()
            (path / "proxy_manager/__pycache__/secret.pyc").write_text("PRIVATE_CACHE\n")
            subprocess.run(["make", "package"], cwd=path, check=True, capture_output=True)
            with tarfile.open(path / "dist/squid-proxy-n8n.tar.gz") as archive:
                names = archive.getnames()
                self.assertIn("config/proxy.example.txt", names)
                self.assertNotIn(".env", names)
                self.assertNotIn("config/proxy.txt", names)
                self.assertFalse(any("__pycache__" in name or ".git/" in name for name in names))


if __name__ == "__main__":
    unittest.main()

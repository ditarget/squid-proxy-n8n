import json
import shutil
import tempfile
import unittest
from pathlib import Path

from proxy_manager.runtime import Runtime
from proxy_manager.settings import Settings


class RuntimeTests(unittest.TestCase):
    def test_invalid_update_does_not_replace_active_config(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_env({
                "ALLOWED_CLIENT_CIDRS": "127.0.0.1/32",
                "RUNTIME_DIR": directory,
                "SQUID_BINARY": shutil.which("false"),
            })
            runtime = Runtime(settings)
            with self.assertRaises(ValueError):
                runtime.stage(b"http://private:SECRET@host:8080")
            self.assertFalse((Path(directory) / "active.conf").exists())
            self.assertEqual(list(Path(directory).glob("version-*")), [])

    def test_status_has_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_env({
                "ALLOWED_CLIENT_CIDRS": "127.0.0.1/32", "RUNTIME_DIR": directory,
            })
            runtime = Runtime(settings)
            runtime.write_status("invalid_proxy_line_1")
            status = json.loads((Path(directory) / "status.json").read_text())
            self.assertIsNone(status["active_digest"])
            self.assertEqual(status["last_update_error"], "invalid_proxy_line_1")


if __name__ == "__main__":
    unittest.main()

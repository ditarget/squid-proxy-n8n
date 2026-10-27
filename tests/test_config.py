import unittest

from proxy_manager.config import render_config
from proxy_manager.inputs import parse_proxies
from proxy_manager.settings import Settings


class ConfigTests(unittest.TestCase):
    def test_unsafe_version_marker_is_rejected(self):
        settings = Settings.from_env({"ALLOWED_CLIENT_CIDRS": "127.0.0.1/32"})
        with self.assertRaises(ValueError):
            render_config(settings, parse_proxies("http://host:8080"), "bad\nhttp_access allow all")

    def test_unvalidated_parent_cannot_inject_configuration(self):
        with self.assertRaises(ValueError):
            parse_proxies("http://host:8080\nhttp_access allow all")


if __name__ == "__main__":
    unittest.main()

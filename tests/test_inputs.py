import unittest

from proxy_manager.inputs import InputError, parse_proxies
from proxy_manager.settings import Settings


class InputTests(unittest.TestCase):
    def test_normalizes_deduplicates_and_decodes_credentials(self):
        peers = parse_proxies(
            "# list\nhttp://u:p%40ss%25word@EXAMPLE.COM:80\n"
            "http://u:p%40ss%25word@example.com\nhttps://other.example:443\n"
        )
        self.assertEqual(len(peers), 2)
        self.assertEqual((peers[0].host, peers[0].port), ("example.com", 80))
        self.assertEqual((peers[0].username, peers[0].password), ("u", "p@ss%word"))
        self.assertTrue(peers[1].tls)

    def test_invalid_input_does_not_expose_secrets(self):
        urls = [
            "", "# empty", "socks4://u:SECRET@host:1080",
            "http://u:SECRET@host:0", "http://u:SECRET@host:65536",
            "http://u:SECRET@host/path", "http://u:SECRET@host?x=1",
            "http://u:SECRET@host#fragment", "http://u:SECRET@host name",
            "http://u:SECRET%0Ahttp_access%20allow%20all@host",
            "http://u:SECRET%ZZ@host", "http://u%3An:SECRET@host",
            "http://u:SECRET@host\\evil", "http://u:SECRET@-host",
            "http://u:SECRET@host:bad", "http://u@host",
        ]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(InputError) as result:
                    parse_proxies(url)
                self.assertNotIn("SECRET", str(result.exception))

    def test_ipv6_parent_is_supported(self):
        peer = parse_proxies("http://[2001:db8::1]:8080")[0]
        self.assertEqual(peer.host, "2001:db8::1")

    def test_peer_repr_does_not_expose_credentials(self):
        peer = parse_proxies("http://private-user:private-pass@host:8080")[0]
        self.assertNotIn("private-user", repr(peer))
        self.assertNotIn("private-pass", repr(peer))

    def test_socks5_and_socks5h_accept_auth_and_remote_dns(self):
        peers = parse_proxies("socks5://user:p%40ss@host\nsocks5h://host:1081")
        self.assertEqual(peers[0].port, 1080)
        self.assertEqual(peers[0].password, "p@ss")
        self.assertTrue(peers[0].socks)
        self.assertFalse(peers[0].tls)
        self.assertTrue(peers[1].socks)

    def test_socks_credentials_obey_byte_limits(self):
        for url in ("socks5://u:@host", "socks5://u:" + "x" * 256 + "@host"):
            with self.assertRaises(InputError):
                parse_proxies(url)


class SettingsTests(unittest.TestCase):
    def test_requires_explicit_client_cidrs(self):
        with self.assertRaises(ValueError):
            Settings.from_env({})

    def test_rejects_invalid_or_unrestricted_client_cidrs(self):
        for value in ("0.0.0.0/0", "::/0", "172.20.0.0/16\nhttp_access allow all", "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    Settings.from_env({"ALLOWED_CLIENT_CIDRS": value})

    def test_settings_normalize_networks_and_validate_intervals(self):
        settings = Settings.from_env({"ALLOWED_CLIENT_CIDRS": "172.20.0.1/16,fd00::/64"})
        self.assertEqual(settings.allowed_cidrs, ("172.20.0.0/16", "fd00::/64"))
        self.assertEqual(settings.poll_interval, 5)
        for value in ("0", "nan", "inf", "-1", "bad"):
            with self.assertRaises(ValueError):
                Settings.from_env({"ALLOWED_CLIENT_CIDRS": "127.0.0.1/32", "POLL_INTERVAL": value})


if __name__ == "__main__":
    unittest.main()

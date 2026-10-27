import hashlib
import os
import signal
import socket
import shutil
import tempfile
import unittest
from pathlib import Path

from fixtures import Deployment, OriginHandler, ParentHandler, Server, certificate, tunnel, wait_for
from proxy_manager.runtime import Runtime, RuntimeErrorSafe, active_version
from proxy_manager.settings import Settings


@unittest.skipUnless(os.environ.get("RUN_SQUID_INTEGRATION") == "1", "run_in_test_container")
class SquidIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("squid"):
            raise RuntimeError("squid_required")
        if any(socket.gethostbyname(host) != "127.0.0.2" for host in ("api.telegram.org", "direct.test")):
            raise RuntimeError("test_host_mappings_required")

    def setUp(self):
        self.resources = []
        self.origin = self.server(OriginHandler, address=("127.0.0.2", 443))

    def tearDown(self):
        for resource in reversed(self.resources):
            resource.close()

    def server(self, handler, **kwargs):
        server = Server(handler, **kwargs)
        self.resources.append(server)
        return server

    def deployment(self, text, **kwargs):
        deployment = Deployment(text, **kwargs)
        self.resources.append(deployment)
        return deployment

    def test_telegram_uses_parents_and_other_destinations_go_direct(self):
        first = self.server(ParentHandler, marker=b"PARENT_ONE")
        second = self.server(ParentHandler, marker=b"PARENT_TWO")
        deployment = self.deployment(f"http://127.0.0.1:{first.port}\nhttp://127.0.0.1:{second.port}\n")
        deployment.ready()
        replies = [tunnel(deployment.port, "api.telegram.org") for _ in range(6)]
        self.assertTrue(all(b"PARENT_" in reply for reply in replies))
        self.assertGreater(len(first.requests), 0)
        self.assertGreater(len(second.requests), 0)
        before = len(first.requests) + len(second.requests)
        self.assertIn(b"DIRECT_ORIGIN", tunnel(deployment.port, "direct.test"))
        self.assertEqual(len(first.requests) + len(second.requests), before)

    def test_atomic_list_replacement_activates_exact_version(self):
        first = self.server(ParentHandler, marker=b"PARENT_ONE")
        second = self.server(ParentHandler, marker=b"PARENT_TWO")
        deployment = self.deployment(f"http://127.0.0.1:{first.port}")
        deployment.ready()
        self.assertIn(b"PARENT_ONE", tunnel(deployment.port, "api.telegram.org"))
        text = f"http://127.0.0.1:{second.port}\n"
        digest = hashlib.sha256(text.encode()).hexdigest()
        deployment.replace(text)
        wait_for(lambda: deployment.status().get("active_digest") == digest)
        self.assertEqual(active_version(deployment.port), digest)
        self.assertIn(b"PARENT_TWO", tunnel(deployment.port, "api.telegram.org"))
        self.assertIsNone(deployment.process.poll())

    def test_invalid_empty_and_missing_updates_keep_last_good_pool(self):
        parent = self.server(ParentHandler)
        deployment = self.deployment(f"http://127.0.0.1:{parent.port}")
        digest = deployment.ready()
        for text in ("http://secret-user:SECRET@host:bad", ""):
            deployment.replace(text)
            wait_for(lambda: deployment.status().get("last_update_error"))
            self.assertEqual(deployment.status()["active_digest"], digest)
            self.assertIn(b"PARENT", tunnel(deployment.port, "api.telegram.org"))
        deployment.proxy_file.unlink()
        wait_for(lambda: deployment.status().get("last_update_error") == "input_unavailable")
        self.assertIn(b"PARENT", tunnel(deployment.port, "api.telegram.org"))
        logs = deployment.logs.read_text()
        self.assertNotIn("SECRET", logs)
        self.assertNotIn("secret-user", logs)

    def test_unavailable_parent_never_connects_directly_to_telegram(self):
        deployment = self.deployment("http://127.0.0.1:1")
        deployment.ready()
        reply = tunnel(deployment.port, "api.telegram.org")
        self.assertNotIn(b"200 Connection established", reply)
        self.assertNotIn(b"DIRECT_ORIGIN", reply)
        self.assertEqual(self.origin.connections, 0)
        self.assertIn(b"DIRECT_ORIGIN", tunnel(deployment.port, "direct.test"))

    def test_special_character_authentication_and_secret_free_logs(self):
        parent = self.server(ParentHandler, auth="fixture-user:p@ss%word:+ space")
        deployment = self.deployment(
            f"http://fixture-user:p%40ss%25word%3A%2B%20space@127.0.0.1:{parent.port}"
        )
        deployment.ready()
        self.assertIn(b"PARENT", tunnel(deployment.port, "api.telegram.org"))
        wait_for(lambda: "ROUTE " in deployment.logs.read_text())
        logs = deployment.logs.read_text()
        self.assertNotIn("fixture-user", logs)
        self.assertNotIn("p@ss", logs)
        self.assertNotIn("p%40ss", logs)

    def test_empty_initial_list_fails_without_starting_squid(self):
        deployment = self.deployment("")
        wait_for(lambda: deployment.process.poll() is not None)
        self.assertNotEqual(deployment.process.returncode, 0)
        self.assertIsNone(active_version(deployment.port))
        self.assertIn("empty_proxy_list", deployment.logs.read_text())

    def test_non_client_address_is_denied(self):
        parent = self.server(ParentHandler)
        deployment = self.deployment(f"http://127.0.0.1:{parent.port}")
        deployment.ready()
        reply = tunnel(deployment.port, "api.telegram.org", source=("127.0.0.3", 0))
        self.assertIn(b"403 Forbidden", reply)
        self.assertEqual(len(parent.requests), 0)

    def test_private_input_owned_by_another_user_is_readable(self):
        parent = self.server(ParentHandler)
        deployment = self.deployment(f"http://127.0.0.1:{parent.port}", input_owner=1000)
        deployment.ready()
        self.assertIn(b"PARENT", tunnel(deployment.port, "api.telegram.org"))

    def test_restoring_identical_file_clears_unavailable_error(self):
        parent = self.server(ParentHandler)
        text = f"http://127.0.0.1:{parent.port}"
        deployment = self.deployment(text)
        digest = deployment.ready()
        deployment.proxy_file.unlink()
        wait_for(lambda: deployment.status().get("last_update_error") == "input_unavailable")
        deployment.replace(text)
        wait_for(lambda: deployment.status() and deployment.status().get("last_update_error") is None, timeout=3)
        self.assertEqual(deployment.status()["active_digest"], digest)

    def test_authenticated_parent_failure_and_407_never_go_direct(self):
        parent = self.server(ParentHandler, auth="fixture:password")
        deployment = self.deployment(f"http://fixture:password@127.0.0.1:{parent.port}")
        deployment.ready()
        self.assertIn(b"PARENT", tunnel(deployment.port, "api.telegram.org"))
        parent.close()
        self.resources.remove(parent)
        self.assertNotIn(b"DIRECT_ORIGIN", tunnel(deployment.port, "api.telegram.org"))
        self.assertEqual(self.origin.connections, 0)
        rejecting = self.server(ParentHandler, auth="fixture:other-password")
        text = f"http://fixture:password@127.0.0.1:{rejecting.port}"
        deployment.replace(text)
        digest = hashlib.sha256(text.encode()).hexdigest()
        wait_for(lambda: deployment.status().get("active_digest") == digest)
        reply = tunnel(deployment.port, "api.telegram.org")
        self.assertNotIn(b"200 Connection established", reply)
        self.assertNotIn(b"DIRECT_ORIGIN", reply)
        self.assertEqual(self.origin.connections, 0)

    def test_unconfirmable_rollback_exits_and_stops_squid(self):
        parent = self.server(ParentHandler)
        text = f"http://127.0.0.1:{parent.port}"
        deployment = self.deployment(text, extra_env={"RELOAD_TIMEOUT": "1"})
        deployment.ready()
        pid = int(Path("/tmp/squid.pid").read_text())
        os.kill(pid, signal.SIGSTOP)
        deployment.replace(text + "\n")
        wait_for(lambda: deployment.process.poll() is not None, timeout=20)
        self.assertNotEqual(deployment.process.returncode, 0)
        self.assertIn("rollback_not_confirmed", deployment.logs.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_https_parent_uses_verified_tls_and_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, context = certificate(directory)
            parent = self.server(ParentHandler, tls=context, auth="fixture:password")
            deployment = self.deployment(
                f"https://fixture:password@127.0.0.1:{parent.port}",
                extra_env={"SSL_CERT_FILE": str(cert)},
            )
            deployment.ready()
            self.assertIn(b"PARENT", tunnel(deployment.port, "api.telegram.org"))
            self.assertGreater(len(parent.requests), 0)
            deployment.close()
            self.resources.remove(deployment)

    def test_https_parent_rejects_untrusted_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            _, context = certificate(directory)
            parent = self.server(ParentHandler, tls=context)
            deployment = self.deployment(f"https://127.0.0.1:{parent.port}")
            deployment.ready()
            reply = tunnel(deployment.port, "api.telegram.org")
            self.assertNotIn(b"200 Connection established", reply)
            self.assertEqual(len(parent.requests), 0)
            self.assertEqual(self.origin.connections, 0)
            deployment.close()
            self.resources.remove(deployment)

    def test_unconfirmed_reload_restores_previous_config(self):
        parent = self.server(ParentHandler)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory).chmod(0o755)
            settings = Settings.from_env({
                "ALLOWED_CLIENT_CIDRS": "127.0.0.1/32",
                "RUNTIME_DIR": str(Path(directory) / "runtime"),
                "RELOAD_TIMEOUT": "2",
            })
            runtime = Runtime(settings)
            raw = f"http://127.0.0.1:{parent.port}".encode()
            try:
                previous = runtime.stage(raw)
                runtime.start(previous)
                candidate = runtime.stage(raw + b"\n")
                candidate.path.write_text(candidate.path.read_text().replace(candidate.digest, "0" * 64))
                with self.assertRaises(RuntimeErrorSafe):
                    runtime.reload(candidate)
                self.assertEqual(active_version(settings.listen_port), previous.digest)
                self.assertIn(b"PARENT", tunnel(settings.listen_port, "api.telegram.org"))
            finally:
                runtime.stop()
            self.assertEqual(runtime.process.returncode, 0)
            self.assertIsNone(active_version(settings.listen_port))


if __name__ == "__main__":
    unittest.main()

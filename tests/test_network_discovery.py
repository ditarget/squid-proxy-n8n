import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NetworkDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.engine = self.path / "engine.sh"
        self.engine.write_text(
            'printf "%s\\n" "$*" >> calls\n'
            'case "$1 $2" in\n'
            '"compose config") printf "CLIENT_NETWORK=%s\\n" "$FAKE_NETWORK"; '
            'test -z "${ALLOWED_CLIENT_CIDRS+x}" || printf "ALLOWED_CLIENT_CIDRS=%s\\n" "$ALLOWED_CLIENT_CIDRS" ;;\n'
            '"network inspect") test "$FAKE_MISSING" != 1 || exit 2; printf "%s\\n" "$FAKE_SUBNETS" ;;\n'
            '"compose up"|"compose run") printf "%s\\n" "$ALLOWED_CLIENT_CIDRS" > applied ;;\n'
            'esac\n'
        )
        self.env = {**os.environ, "COMPOSE_COMMAND": f"sh {self.engine} compose",
                    "ENGINE_COMMAND": f"sh {self.engine}", "FAKE_NETWORK": "client-selected",
                    "FAKE_SUBNETS": "172.23.0.0/20", "FAKE_MISSING": "0"}
        self.env.pop("ALLOWED_CLIENT_CIDRS", None)

    def run_wrapper(self):
        return subprocess.run(["sh", str(ROOT / "compose-client.sh"), "up", "-d", "squid"],
                              cwd=self.path, env=self.env, capture_output=True, text=True)

    def test_default_and_auto_inspect_only_named_client_network(self):
        for value in (None, "auto"):
            with self.subTest(value=value):
                if value is not None:
                    self.env["ALLOWED_CLIENT_CIDRS"] = value
                result = self.run_wrapper()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.path / "applied").read_text(), "172.23.0.0/20\n")
                calls = (self.path / "calls").read_text().splitlines()
                inspected = [line for line in calls if line.startswith("network inspect")]
                self.assertTrue(inspected)
                self.assertTrue(all(line.endswith("-- client-selected") for line in inspected))

    def test_dual_stack_ipam_and_recreated_network_are_resolved_each_run(self):
        self.env["FAKE_SUBNETS"] = "172.23.0.0/20\nfd42::/64\n"
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual((self.path / "applied").read_text(), "172.23.0.0/20,fd42::/64\n")
        self.env["FAKE_SUBNETS"] = "10.28.1.0/24"
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual((self.path / "applied").read_text(), "10.28.1.0/24\n")

    def test_manual_override_skips_ipam_inspection(self):
        self.env.update(ALLOWED_CLIENT_CIDRS="10.42.0.0/24,fd42::/64", FAKE_MISSING="1")
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.path / "applied").read_text(), "10.42.0.0/24,fd42::/64\n")
        self.assertNotIn("network inspect", (self.path / "calls").read_text())

    def test_missing_network_empty_ipam_and_missing_name_do_not_launch(self):
        for overrides in ({"FAKE_MISSING": "1"}, {"FAKE_SUBNETS": ""}, {"FAKE_NETWORK": ""}):
            with self.subTest(overrides=overrides):
                env = dict(self.env)
                self.env.update(overrides)
                result = self.run_wrapper()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.path / "applied").exists())
                self.env = env

    def test_make_up_and_check_use_discovered_subnets(self):
        for target in ("up", "check"):
            with self.subTest(target=target):
                result = subprocess.run(
                    ["make", "-f", str(ROOT / "Makefile"), target,
                     f"COMPOSE=sh {self.engine} compose", f"ENGINE=sh {self.engine}"],
                    cwd=self.path, env=self.env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.path / "applied").read_text(), "172.23.0.0/20\n")

    def test_podman_global_flags_keep_podman_ipam_format(self):
        engine = self.path / "podman"
        engine.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = --log-level=error ]; then shift; fi\n'
            'if [ "$1" = network ]; then case "$4" in *".Subnets"*) ;; *) exit 9 ;; esac; fi\n'
            + self.engine.read_text()
        )
        engine.chmod(0o700)
        self.env["ENGINE_COMMAND"] = f"{engine} --log-level=error"
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.path / "applied").read_text(), "172.23.0.0/20\n")


if __name__ == "__main__":
    unittest.main()

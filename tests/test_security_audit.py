import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STUB = """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['AUDIT_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
if name == 'docker':
    if args[0] == 'build':
        sys.exit(int(os.environ.get('BUILD_STATUS', '0')))
    mode = next(a.split('=', 1)[1] for a in args if a.startswith('AUDIT_MODE='))
    script = args[-1].replace('cd /app', 'cd ' + os.environ['AUDIT_TEST_ROOT'])
    env = {**os.environ, 'AUDIT_MODE': mode}
    sys.exit(subprocess.call(['sh', '-ec', script], env=env))
if name == 'node':
    print('12.14.0')
if name == 'pnpm':
    key = 'PROD_STATUS' if '--prod' in args else 'ALL_STATUS'
    sys.exit(int(os.environ.get(key, '0')))
if name in ('uv', 'uvx'):
    sys.exit(int(os.environ.get(name.upper() + '_STATUS', '0')))
"""


class SecurityAuditTests(unittest.TestCase):
    def run_audit(self, mode="gate", **statuses):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("docker", "node", "npm", "pnpm", "uv", "uvx"):
                tool = folder / name
                tool.write_text(
                    STUB.replace("#!/usr/bin/env python3", f"#!{sys.executable}")
                )
                tool.chmod(0o755)
            log = folder / "commands.jsonl"
            result = subprocess.run(
                ["sh", str(ROOT / "scripts/security-audit.sh"), mode],
                env={
                    **os.environ,
                    "PATH": os.pathsep.join((str(folder), os.environ["PATH"])),
                    "AUDIT_TEST_LOG": str(log),
                    "AUDIT_TEST_ROOT": str(ROOT),
                    **{key: str(value) for key, value in statuses.items()},
                },
                capture_output=True,
                text=True,
                check=False,
            )
            commands = (
                [json.loads(line) for line in log.read_text().splitlines()]
                if log.exists()
                else []
            )
            return result.returncode, commands

    def test_gate_uses_npm_thresholds_without_python_audit(self):
        status, commands = self.run_audit()
        self.assertEqual(status, 0)
        self.assertEqual(
            [c for c in commands if c[0] == "pnpm"],
            [
                ["pnpm", "audit", "--prod", "--audit-level", "high"],
                ["pnpm", "audit", "--audit-level", "critical"],
            ],
        )
        self.assertFalse(
            any(
                c[0] in ("uv", "uvx") or c[:2] == ["docker", "build"] for c in commands
            )
        )

    def test_runtime_high_findings_fail_gate(self):
        status, _ = self.run_audit(PROD_STATUS=1)
        self.assertNotEqual(status, 0)

    def test_development_critical_findings_fail_gate(self):
        status, _ = self.run_audit(ALL_STATUS=1)
        self.assertNotEqual(status, 0)

    def test_reporting_checks_both_ecosystems_after_python_findings(self):
        status, commands = self.run_audit("report", UVX_STATUS=1)
        self.assertNotEqual(status, 0)
        self.assertTrue(any(c[0] == "uvx" for c in commands))
        self.assertIn(["pnpm", "audit", "--audit-level", "high"], commands)

    def test_failed_export_does_not_audit_stale_requirements(self):
        status, commands = self.run_audit("report", UV_STATUS=1)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(c[0] == "uvx" for c in commands))
        self.assertTrue(any(c[0] == "pnpm" for c in commands))

    def test_failed_build_does_not_audit_stale_image(self):
        status, commands = self.run_audit("report", BUILD_STATUS=1)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(c[0] == "uvx" for c in commands))
        self.assertTrue(any(c[0] == "pnpm" for c in commands))

    def test_invalid_mode_does_not_run_docker(self):
        status, commands = self.run_audit("invalid")
        self.assertEqual(status, 2)
        self.assertEqual(commands, [])

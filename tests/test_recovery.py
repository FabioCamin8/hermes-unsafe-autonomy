from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
BACKUP = REPOSITORY / "scripts" / "backup.sh"
ROLLBACK = REPOSITORY / "scripts" / "rollback.sh"


class RecoveryScriptTests(unittest.TestCase):
    def test_backup_and_rollback_preserve_mcp_runtime_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            hermes_home = home / ".hermes"
            config = hermes_home / "config.yaml"
            runtime = home / ".local" / "share" / "hermes-autonomy" / "npm"
            runtime_bin = runtime / "node_modules" / ".bin"
            local_bin = home / ".local" / "bin"
            browser_profile = home / ".local" / "share" / "hermes" / "chromium-profile"
            runtime_bin.mkdir(parents=True)
            local_bin.mkdir(parents=True)
            browser_profile.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            config.write_text("memory:\n  provider: hermes_vault\n", encoding="utf-8")
            (browser_profile / "cookie.sqlite").write_text("browser data\n", encoding="utf-8")
            for name in ("codex", "chrome-devtools-mcp"):
                target = runtime_bin / name
                target.write_text(f"runtime-{name}\n", encoding="utf-8")
                os.symlink(target, local_bin / name)

            environment = os.environ.copy()
            environment.update({"HOME": str(home), "HERMES_HOME": str(hermes_home)})
            run_environment = {"cwd": home, "env": environment, "check": True}
            backup_result = subprocess.run(
                [str(BACKUP), "--label", "recovery-test"],
                capture_output=True,
                text=True,
                **run_environment,
            )
            backup_line = next(
                line for line in backup_result.stdout.splitlines() if line.startswith("BACKUP_DIR=")
            )
            backup_dir = Path(backup_line.removeprefix("BACKUP_DIR="))
            self.assertTrue((backup_dir / "user-config/local-share/hermes-autonomy/npm").is_dir())
            self.assertTrue((backup_dir / "user-config/local-bin/codex").is_symlink())
            self.assertFalse((backup_dir / "user-config/local-share/hermes/chromium-profile").exists())

            config.write_text("memory:\n  provider: changed\n", encoding="utf-8")
            for name in ("codex", "chrome-devtools-mcp"):
                (local_bin / name).unlink()
                (local_bin / name).write_text("dereferenced wrapper\n", encoding="utf-8")
            shutil.rmtree(runtime)

            subprocess.run(
                [str(ROLLBACK), "--backup-dir", str(backup_dir)],
                capture_output=True,
                text=True,
                **run_environment,
            )

            self.assertIn("provider: hermes_vault", config.read_text(encoding="utf-8"))
            for name in ("codex", "chrome-devtools-mcp"):
                launcher = local_bin / name
                self.assertTrue(launcher.is_symlink())
                self.assertEqual(os.readlink(launcher), str(runtime_bin / name))
                self.assertEqual((runtime_bin / name).read_text(encoding="utf-8"), f"runtime-{name}\n")


if __name__ == "__main__":
    unittest.main()

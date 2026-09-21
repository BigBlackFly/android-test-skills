"""使用隔离目录验证同步脚本会复制 preparation 子包，不携带缓存。"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("pwsh"), "需要 PowerShell 7")
class TestPreparationSync(unittest.TestCase):
    def test_nested_code_syncs_both_directions_without_cache(self):
        with tempfile.TemporaryDirectory(prefix="dsh-sync-test-") as temp:
            base = Path(temp)
            skill, workspace = base / "skill", base / "workspace"
            workspace.mkdir()
            (skill / "scripts").mkdir(parents=True)
            script = skill / "scripts/sync_skill.ps1"
            shutil.copy2(ROOT / "scripts/sync_skill.ps1", script)
            prep = skill / "framework/preparation"
            (prep / "__pycache__").mkdir(parents=True)
            (prep / "__init__.py").write_text("", encoding="utf-8")
            modules = ("grant_app_permissions.py", "reset_sdcard_files.py", "app_packages.py")
            for module in modules:
                (prep / module).write_text("original", encoding="utf-8")
            (prep / "__pycache__/grant_app_permissions.cpython-311.pyc").write_bytes(b"cache")
            (prep / "legacy.pyc").write_bytes(b"cache")
            env = dict(os.environ, DSH_WORKSPACE_DIR=str(workspace))
            for reverse in (False, True):
                if reverse:
                    for module in modules:
                        (workspace / "framework/preparation" / module).write_text("updated", encoding="utf-8")
                command = [shutil.which("pwsh"), "-NoProfile", "-File", str(script)]
                if reverse:
                    command.append("-ToSkill")
                result = subprocess.run(command, env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                target = skill if reverse else workspace
                for module in modules:
                    self.assertEqual((target / "framework/preparation" / module).read_text(encoding="utf-8"),
                                     "updated" if reverse else "original")
            self.assertTrue((workspace / "framework/preparation/__init__.py").is_file())
            self.assertFalse(list(workspace.rglob("*.pyc")))


if __name__ == "__main__":
    unittest.main()

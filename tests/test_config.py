import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_dotenv_supplies_unset_values_without_overriding_environment(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    shutil.copy(repository_root / "config.py", tmp_path / "config.py")
    (tmp_path / ".env").write_text(
        "DATABASE_PATH=from-dotenv.sqlite3\nEBAY_CURRENCY=EUR\n",
        encoding="utf-8",
    )

    script = """
import os
import config

assert config.Config.database_path == "from-process.sqlite3"
assert config.Config.currency == "EUR"
assert os.environ["DATABASE_PATH"] == "from-process.sqlite3"
"""
    isolated_environment = {
        "DATABASE_PATH": "from-process.sqlite3",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=isolated_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, "isolated config import failed"
    assert result.stdout == ""
    assert result.stderr == ""


def test_backup_directory_has_local_default(monkeypatch):
    monkeypatch.delenv("BACKUP_DIRECTORY", raising=False)
    from config import Config
    assert Config().backup_directory == "instance/backups"

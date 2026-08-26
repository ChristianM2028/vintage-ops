import hashlib
import io
import json
import os
import sqlite3
import tarfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from backups import ARCHIVE_RE, BackupError, create_backup, prune_backups
from config import Config


def backup_app(tmp_path):
    cfg = replace(
        Config(),
        database_path=str(tmp_path / "state" / "app.sqlite3"),
        upload_directory=str(tmp_path / "state" / "uploads"),
        backup_directory=str(tmp_path / "backups"),
    )
    return create_app(cfg), cfg


def test_cli_creates_restorable_allowlisted_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOULD_NEVER_BE_BACKED_UP", "environment-secret-fixture")
    app, cfg = backup_app(tmp_path)
    upload = Path(cfg.upload_directory) / "nested" / "photo.jpg"
    upload.parent.mkdir()
    upload.write_bytes(b"photo-bytes\x00\xff")
    token = b"encrypted-token-bytes"
    with sqlite3.connect(cfg.database_path) as connection:
        connection.execute("CREATE TABLE backup_fixture (token BLOB)")
        connection.execute("INSERT INTO backup_fixture VALUES (?)", (token,))

    result = app.test_cli_runner().invoke(args=["create-backup"])
    assert result.exit_code == 0
    archive_path = Path(result.output.splitlines()[0])
    assert ARCHIVE_RE.fullmatch(archive_path.name)
    assert stat_mode(archive_path) == 0o600
    assert stat_mode(archive_path.parent) == 0o700

    with tarfile.open(archive_path) as archive:
        assert set(archive.getnames()) == {
            "database.sqlite3", "manifest.json", "uploads/nested/photo.jpg"
        }
        assert archive.extractfile("uploads/nested/photo.jpg").read() == upload.read_bytes()
        manifest_bytes = archive.extractfile("manifest.json").read()
        manifest = json.loads(manifest_bytes)
        assert set(manifest) == {
            "format_version", "application", "created_at", "database",
            "integrity_check", "photo_count", "photos", "files",
        }
        assert isinstance(manifest["files"], list)
        assert all(set(record) == {"path", "size", "sha256"} for record in manifest["files"])
        database_bytes = archive.extractfile("database.sqlite3").read()
        photo_bytes = archive.extractfile("uploads/nested/photo.jpg").read()
        assert manifest["files"] == [
            {
                "path": "database.sqlite3",
                "size": len(database_bytes),
                "sha256": hashlib.sha256(database_bytes).hexdigest(),
            },
            {
                "path": "uploads/nested/photo.jpg",
                "size": len(photo_bytes),
                "sha256": hashlib.sha256(photo_bytes).hexdigest(),
            },
        ]
        snapshot = tmp_path / "restored.sqlite3"
        snapshot.write_bytes(database_bytes)
    assert b"environment-secret-fixture" not in archive_path.read_bytes()
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("SELECT token FROM backup_fixture").fetchone()[0] == token


def stat_mode(path):
    return path.stat().st_mode & 0o777


def test_upload_symlinks_and_unrelated_database_files_are_excluded(tmp_path):
    app, cfg = backup_app(tmp_path)
    uploads = Path(cfg.upload_directory)
    outside = tmp_path / "outside-secret"
    outside.write_text("plaintext OAuth token fixture")
    (uploads / "linked").symlink_to(outside)
    Path(cfg.database_path + "-wal").write_text("wal fixture")
    Path(cfg.database_path + "-shm").write_text("shm fixture")
    (uploads / ".env").symlink_to(outside)
    result = app.test_cli_runner().invoke(args=["create-backup"])
    assert result.exit_code == 0
    with tarfile.open(Path(result.output.splitlines()[0])) as archive:
        assert archive.getnames() == ["database.sqlite3", "manifest.json"]


def test_collision_corruption_and_unsafe_paths_leave_no_temporaries(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not sqlite")
    backups = tmp_path / "backups"
    with pytest.raises(BackupError):
        create_backup(corrupt, uploads, backups)
    assert not list(backups.glob(".*"))
    valid = tmp_path / "valid.sqlite"
    sqlite3.connect(valid).close()
    fixed = datetime(2026, 8, 25, 12, 34, 56, 123456, tzinfo=timezone.utc)
    create_backup(valid, uploads, backups, now=fixed)
    with pytest.raises(BackupError, match="timestamp"):
        create_backup(valid, uploads, backups, now=fixed)
    with pytest.raises(BackupError):
        create_backup(valid, uploads, uploads)
    with pytest.raises(BackupError):
        create_backup(valid, uploads, Path(uploads.anchor))


def test_retention_is_bounded_non_recursive_and_supports_dry_run(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    names = []
    for days in (1, 5, 10):
        stamp = (now - timedelta(days=days)).strftime("%Y%m%dT%H%M%S.000000Z")
        name = f"vintage-ops-backup-{stamp}.tar.gz"
        (root / name).write_bytes(b"archive")
        names.append(name)
    (root / "unrelated.tar.gz").write_bytes(b"leave")
    nested = root / "nested"
    nested.mkdir()
    (nested / names[-1]).write_bytes(b"leave")
    (root / "link").symlink_to(root / names[-1])

    selected = prune_backups(root, keep_count=1, dry_run=True)
    assert selected == names[1:]
    assert all((root / name).exists() for name in names)
    assert prune_backups(root, keep_days=5, now=now) == [names[-1]]
    assert (root / "unrelated.tar.gz").exists() and (nested / names[-1]).exists()
    with pytest.raises(BackupError):
        prune_backups(root)
    with pytest.raises(BackupError):
        prune_backups(root, keep_count=0)
    with pytest.raises(BackupError):
        prune_backups(Path(root.anchor), keep_count=1)


def test_cli_failure_is_nonzero_and_does_not_echo_secret_paths(tmp_path):
    app, cfg = backup_app(tmp_path)
    secret = "super-secret-oauth-value"
    Path(cfg.database_path).write_text(secret)
    result = app.test_cli_runner().invoke(args=["create-backup"])
    assert result.exit_code != 0
    assert secret not in result.output

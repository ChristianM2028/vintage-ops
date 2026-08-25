import base64
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

import app as app_module
from app import create_app
from backup_storage import BackupStorageError, decrypt_archive, encrypt_archive
from config import Config


def key(byte=b"k"):
    return base64.urlsafe_b64encode(byte*32).decode("ascii")


def test_versioned_authenticated_encryption_is_confidential_and_random(tmp_path):
    plain=tmp_path/"plain.tar.gz"; plain.write_bytes(b"recognizable plaintext archive")
    first=tmp_path/"first.enc"; second=tmp_path/"second.enc"
    one=encrypt_archive(plain,first,key()); two=encrypt_archive(plain,second,key())
    assert plain.read_bytes() not in first.read_bytes()
    assert first.read_bytes()!=second.read_bytes()
    assert one["plaintext_sha256"]==two["plaintext_sha256"]==hashlib.sha256(plain.read_bytes()).hexdigest()
    restored=tmp_path/"restored"
    assert decrypt_archive(first,restored,key())==one["plaintext_sha256"]
    assert restored.read_bytes()==plain.read_bytes()
    payload=bytearray(first.read_bytes()); payload[-1]^=1; first.write_bytes(payload)
    with pytest.raises(BackupStorageError,match="authentication"):
        decrypt_archive(first,restored,key())
    with pytest.raises(BackupStorageError,match="authentication"):
        decrypt_archive(second,restored,key(b"x"))


def test_backup_key_never_falls_back_to_token_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY",key())
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY",raising=False)
    assert Config().backup_encryption_key==""


def test_scheduled_backup_is_post_only_authenticated_leased_and_sanitized(tmp_path,monkeypatch):
    cfg=replace(Config(),database_path=str(tmp_path/"db.sqlite"),upload_directory=str(tmp_path/"uploads"),
                backup_directory=str(tmp_path/"backups"),backup_scheduled_job_token="s"*48)
    flask_app=create_app(cfg); client=flask_app.test_client()
    assert client.get("/internal/scheduled-backup").status_code==405
    assert client.post("/internal/scheduled-backup").get_json()=={"status":"unauthorized"}
    monkeypatch.setattr(app_module,"orchestrate_backup",lambda cfg,db:{"status":"ok","date":"2026-08-25","disposition":"created","verified_size":123,"checksum_status":"verified","retained":1,"deleted":0,"secret":"must not escape"})
    response=client.post("/internal/scheduled-backup",headers={"Authorization":"Bearer "+"s"*48})
    assert response.status_code==200
    assert response.get_json()=={"status":"ok","date":"2026-08-25","disposition":"created","verified_size":123,"checksum_status":"verified","retained":1,"deleted":0}


def test_caller_is_volume_free_by_construction():
    source=Path("scripts/run_scheduled_backup.py").read_text()
    assert "sqlite" not in source.lower()
    assert "flask" not in source.lower()
    assert "/data" not in source
    assert "BACKUP_S3_" not in source

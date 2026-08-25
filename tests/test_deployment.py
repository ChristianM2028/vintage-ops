import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from flask import url_for
from werkzeug.security import generate_password_hash

from app import create_app
from config import Config


def deployment_config(tmp_path, **overrides):
    values={"database_path":str(tmp_path / "state" / "app.sqlite3"),
            "upload_directory":str(tmp_path / "state" / "uploads")}
    values.update(overrides)
    return replace(Config(),**values)


def test_checked_in_launch_contract():
    launch=Path("start.sh").read_text()
    assert "app:create_app()" in launch
    assert '0.0.0.0:${PORT}' in launch
    assert "--workers 1" in launch
    assert "--threads 4" in launch
    assert "exec gunicorn" in launch
    railway=Path("railway.toml").read_text()
    assert 'DATABASE_PATH = "/data/vintage_ops.sqlite3"' in railway
    assert 'UPLOAD_DIRECTORY = "/data/uploads"' in railway
    assert 'BACKUP_DIRECTORY = "/data/backups"' in railway


def test_railway_web_and_backup_service_contracts():
    with Path("railway.toml").open("rb") as config_file:
        web_config=tomllib.load(config_file)
    with Path("railway-backup.toml").open("rb") as config_file:
        backup_config=tomllib.load(config_file)

    assert web_config["build"] == {
        "builder":"DOCKERFILE", "dockerfilePath":"Dockerfile"}
    assert web_config["deploy"]["startCommand"] == "/app/start.sh"
    assert web_config["deploy"]["healthcheckPath"] == "/healthz"
    assert web_config["deploy"]["restartPolicyType"] == "ON_FAILURE"
    assert web_config["variables"]["DATABASE_PATH"] == "/data/vintage_ops.sqlite3"
    assert web_config["variables"]["UPLOAD_DIRECTORY"] == "/data/uploads"
    assert web_config["variables"]["BACKUP_DIRECTORY"] == "/data/backups"

    assert backup_config["build"] == {
        "builder":"DOCKERFILE", "dockerfilePath":"Dockerfile"}
    assert backup_config["deploy"]["startCommand"] == (
        "python scripts/run_scheduled_backup.py")
    assert backup_config["deploy"]["restartPolicyType"] == "NEVER"
    assert not any(key.startswith("healthcheck") for key in backup_config["deploy"])
    assert "variables" not in backup_config

    readme=Path("README.md").read_text()
    assert "/railway-backup.toml" in readme
    assert "15 3 * * *" in readme


def test_startup_uses_only_configured_persistent_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg=deployment_config(tmp_path)
    app=create_app(cfg)
    assert Path(cfg.database_path).is_file()
    assert Path(cfg.upload_directory).is_dir()
    assert not (tmp_path / "instance").exists()
    assert app.test_client().get("/healthz").get_json()=={"status":"ok"}


def test_upload_path_must_be_directory(tmp_path):
    upload=tmp_path / "upload"; upload.write_text("not a directory")
    with pytest.raises((RuntimeError,FileExistsError)):
        create_app(deployment_config(tmp_path,upload_directory=str(upload)))


def test_health_is_public_and_non_sensitive(tmp_path):
    cfg=deployment_config(tmp_path,seller_username="seller",
                          seller_password_hash=generate_password_hash("secret"),
                          stripe_secret_key="sk_test_do_not_disclose")
    response=create_app(cfg).test_client().get("/healthz")
    assert response.status_code==200
    assert response.get_json()=={"status":"ok"}
    assert b"secret" not in response.data and b"sqlite" not in response.data


def _origin_app(tmp_path, **values):
    app=create_app(deployment_config(tmp_path,**values))
    app.add_url_rule("/_origin", "origin", lambda: url_for("healthz",_external=True))
    return app


def test_one_trusted_proxy_hop_controls_external_origin(tmp_path):
    app=_origin_app(tmp_path,proxy_fix_proto=1,proxy_fix_host=1)
    response=app.test_client().get("/_origin",headers={
        "X-Forwarded-Proto":"http, https", "X-Forwarded-Host":"evil.example, shop.up.railway.app"})
    assert response.text=="https://shop.up.railway.app/healthz"


def test_forwarded_headers_are_ignored_when_untrusted(tmp_path):
    app=_origin_app(tmp_path,proxy_fix_proto=0,proxy_fix_host=0)
    response=app.test_client().get("/_origin",base_url="http://internal",
        headers={"X-Forwarded-Proto":"https","X-Forwarded-Host":"evil.example"})
    assert response.text=="http://internal/healthz"


def test_secure_session_cookie_behind_https_proxy(tmp_path):
    app=_origin_app(tmp_path,proxy_fix_proto=1,proxy_fix_host=1,session_cookie_secure=True,
        seller_username="seller",seller_password_hash=generate_password_hash("secret"))
    client=app.test_client(); headers={"X-Forwarded-Proto":"https","X-Forwarded-Host":"shop.up.railway.app"}
    page=client.get("/login",headers=headers)
    token=page.text.split('name="csrf_token" value="',1)[1].split('"',1)[0]
    response=client.post("/login",data={"csrf_token":token,"username":"seller","password":"secret"},headers=headers)
    cookie=response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_cli_bounds_repeatability_and_production_refusal(tmp_path):
    app=create_app(deployment_config(tmp_path))
    runner=app.test_cli_runner()
    assert runner.invoke(args=["storefront-maintenance","--limit","1"]).exit_code==0
    assert runner.invoke(args=["storefront-maintenance","--limit","1"]).exit_code==0
    assert runner.invoke(args=["storefront-maintenance","--limit","0"]).exit_code!=0
    production=create_app(deployment_config(tmp_path / "prod",environment="production"))
    result=production.test_cli_runner().invoke(args=["ebay-reconcile-orders","--limit","1"])
    assert result.exit_code!=0 and "Sandbox" in result.output


def test_live_stripe_key_still_fails():
    with pytest.raises(ValueError,match="Live Stripe"):
        replace(Config(),stripe_secret_key="sk_live_forbidden")

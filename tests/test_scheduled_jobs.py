import logging
from dataclasses import replace

from app import create_app
from config import Config
from db import Database
from scheduled_jobs import run_scheduled_job


def configured(tmp_path, token=""):
    return Config(database_path=str(tmp_path/"jobs.sqlite3"),upload_directory=str(tmp_path/"uploads"),scheduled_job_token=token)


def test_endpoint_authentication_is_fail_closed_and_sanitized(tmp_path, caplog):
    secret="high-entropy-secret-that-is-long-enough-123456"
    for cfg,headers in ((configured(tmp_path/"unset"),{}),(configured(tmp_path/"missing",secret),{}),
                        (configured(tmp_path/"malformed",secret),{"Authorization":secret}),
                        (configured(tmp_path/"wrong",secret),{"Authorization":"Bearer supplied-secret"})):
        response=create_app(cfg).test_client().post("/internal/scheduled-maintenance",headers=headers)
        assert response.status_code==401 and response.json=={"status":"unauthorized"}
        assert secret not in response.text and "supplied-secret" not in response.text
    caplog.set_level(logging.INFO)
    app=create_app(configured(tmp_path/"valid",secret))
    response=app.test_client().post("/internal/scheduled-maintenance",headers={"Authorization":"Bearer "+secret})
    assert response.status_code==200 and response.json["status"]=="ok"
    assert app.test_client().get("/internal/scheduled-maintenance").status_code==405
    assert secret not in caplog.text


def test_lease_busy_expiry_and_ownership(tmp_path):
    db=Database(str(tmp_path/"lease.sqlite3")); db.migrate()
    first=db.acquire_scheduled_job("job",60)
    assert first["disposition"]=="acquired"
    assert db.acquire_scheduled_job("job",60)["disposition"]=="busy"
    assert not db.finish_scheduled_job("job","wrong",True)
    with db.connect() as con:
        con.execute("UPDATE scheduled_job_leases SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_name='job'")
    second=db.acquire_scheduled_job("job",60)
    assert second["disposition"]=="acquired" and second["ownership_token"]!=first["ownership_token"]
    assert not db.finish_scheduled_job("job",first["ownership_token"],True)
    assert db.finish_scheduled_job("job",second["ownership_token"],True)


def test_runner_sanitizes_provider_failure(tmp_path):
    db=Database(str(tmp_path/"failure.sqlite3")); db.migrate()
    class Provider:
        def reconcile_orders(self,max_orders): raise RuntimeError("customer token https://sensitive.invalid")
    result=run_scheduled_job(db,Provider())
    assert result["status"]=="failed" and result["failure_categories"]==["internal"]
    assert "sensitive" not in str(result) and "token" not in str(result)

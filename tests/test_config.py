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

import itertools
import json
import pytest


def _isolated_config(environment):
    root = Path(__file__).resolve().parents[1]
    script = "import json; from config import Config; c=Config(); print(json.dumps([c.stripe_identity,c.ebay_integration_identity,c.stripe_is_fake,c.ebay_is_mock]))"
    result = subprocess.run([sys.executable, "-c", script], cwd=root, env=environment,
                            capture_output=True, text=True, check=False)
    return result


@pytest.mark.parametrize("app_environment,stripe_mode,ebay_mode", itertools.product(
    ("local", "sandbox", "production"), ("fake", "test", "live"),
    ("mock", "sandbox", "production")))
def test_authoritative_provider_matrix(app_environment, stripe_mode, ebay_mode):
    fernet="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
    env={"PATH":os.environ.get("PATH", ""), "PYTHONPATH":str(Path(__file__).resolve().parents[1]),
         "APP_ENVIRONMENT":app_environment,"STRIPE_PROVIDER_MODE":stripe_mode,
         "EBAY_PROVIDER_MODE":ebay_mode,"STRIPE_SECRET_KEY":"sk_test_example" if stripe_mode=="test" else ("sk_live_example" if stripe_mode=="live" else ""),
         "FLASK_SECRET_KEY":"x"*40,"STRIPE_WEBHOOK_SECRET":"x"*40,"SELLER_USERNAME":"seller",
         "SELLER_PASSWORD_HASH":"x"*40,"SCHEDULED_JOB_TOKEN":"x"*40,"BACKUP_SCHEDULED_JOB_TOKEN":"x"*40,
         "TOKEN_ENCRYPTION_KEY":fernet,"BACKUP_ENCRYPTION_KEY":fernet,"SESSION_GENERATION":"x"*40,
         "SESSION_COOKIE_SECURE":"true","CANONICAL_ORIGIN":"https://example.test",
         "STRIPE_SUCCESS_URL":"https://example.test/{ORDER_ID}/success","STRIPE_CANCEL_URL":"https://example.test/{ORDER_ID}/cancel",
         "PRICING_LEGAL_REVIEWED":"true","PRODUCTION_PROVIDER_ACTIVATION_ID":"x"*40,
         "EXPECTED_STRIPE_ACCOUNT_ID":"acct_123456","EXPECTED_EBAY_SELLER_ID":"seller1",
         "RATE_LIMIT_STORAGE_URI":"redis://example","DATABASE_PATH":"/data/app.sqlite",
         "UPLOAD_DIRECTORY":"/data/uploads","BACKUP_DIRECTORY":"/data/backups"}
    result=_isolated_config(env)
    accepted=(app_environment in {"local","sandbox"} and stripe_mode in {"fake","test"} and ebay_mode in {"mock","sandbox"}) or (app_environment=="production" and stripe_mode=="live" and ebay_mode=="production")
    assert (result.returncode==0) is accepted, result.stderr
    if accepted:
        assert json.loads(result.stdout)==[
            {"fake":"fake","test":"stripe_test","live":"stripe_live"}[stripe_mode],
            {"mock":"local_mock","sandbox":"ebay_sandbox","production":"ebay_production"}[ebay_mode],
            stripe_mode=="fake",ebay_mode=="mock"]
    else:
        assert "Provider modes do not match APP_ENVIRONMENT" in result.stderr


@pytest.mark.parametrize("spelling,value", [("1",True),("true",True),("yes",True),("on",True),
                                               ("0",False),("false",False),("no",False),("off",False)])
@pytest.mark.parametrize("variable,mode,matching", [
    ("STRIPE_FAKE_MODE","fake",True),("STRIPE_FAKE_MODE","test",False),
    ("MARKETPLACE_MOCK_MODE","mock",True),("MARKETPLACE_MOCK_MODE","sandbox",False)])
def test_legacy_boolean_alias_spellings(spelling,value,variable,mode,matching):
    env={"PATH":os.environ.get("PATH", ""),"PYTHONPATH":str(Path(__file__).resolve().parents[1]),
         "APP_ENVIRONMENT":"local","STRIPE_PROVIDER_MODE":"fake" if variable.startswith("MARKET") else mode,
         "EBAY_PROVIDER_MODE":"mock" if variable.startswith("STRIPE") else mode,
         "STRIPE_SECRET_KEY":"sk_test_example" if mode=="test" else "",variable:spelling}
    result=_isolated_config(env)
    expected=value==matching
    assert (result.returncode==0) is expected
    if not expected: assert f"{variable} conflicts" in result.stderr


@pytest.mark.parametrize("mode,alias,accepted", [("mock","sandbox",True),("sandbox","sandbox",True),
                                                   ("production","production",False),("mock","production",False),
                                                   ("sandbox","production",False)])
def test_legacy_ebay_environment_alias(mode,alias,accepted):
    env={"PATH":os.environ.get("PATH", ""),"PYTHONPATH":str(Path(__file__).resolve().parents[1]),
         "APP_ENVIRONMENT":"local","STRIPE_PROVIDER_MODE":"fake","EBAY_PROVIDER_MODE":mode,
         "EBAY_ENVIRONMENT":alias}
    result=_isolated_config(env)
    assert (result.returncode==0) is accepted
    if not accepted and mode!="production": assert "EBAY_ENVIRONMENT conflicts" in result.stderr

from cryptography.fernet import Fernet
import app as app_module
from app import create_app
from config import Config

def test_item_page_and_sandbox_guard(tmp_path):
    cfg=Config(database_path=str(tmp_path/"app.sqlite"),encryption_key=Fernet.generate_key().decode())
    app=create_app(cfg); db=app.extensions["vintage_db"]
    item=db.create_item(sku="S",title="Test",description="D",price="1",currency="USD",quantity=1,condition="3000",brand="B",size="M",color="C",category="1",photo_urls=["https://x.test/i"])
    response=app.test_client().get(f"/items/{item['id']}"); assert response.status_code==200 and b"eBay lifecycle" in response.data
    assert create_app(Config(database_path=str(tmp_path/"prod.sqlite"),environment="production")).test_client().get("/sandbox").status_code==404

def test_browser_flow_flag_and_csrf_guard(tmp_path):
    hidden=create_app(Config(database_path=str(tmp_path/"hidden.sqlite")))
    db=hidden.extensions["vintage_db"]
    item=db.create_item(title="Hidden",description="D",price="1",currency="USD",quantity=1,condition="Used",category="Clothes",photo_urls=[])
    client=hidden.test_client()
    assert b"Run eBay Browser Flow" not in client.get(f"/items/{item['id']}").data
    assert client.post(f"/items/{item['id']}/test-ebay-browser-flow").status_code==400

    shown=create_app(Config(database_path=str(tmp_path/"shown.sqlite"),test_ebay_browser_flow=True,
                            ebay_browser_user_data_directory=str(tmp_path/"profile")))
    db=shown.extensions["vintage_db"]
    item=db.create_item(title="Shown",description="D",price="1",currency="USD",quantity=1,condition="Used",category="Clothes",photo_urls=[])
    response=shown.test_client().get(f"/items/{item['id']}")
    assert b"Run eBay Browser Flow (test-only)" in response.data
    assert b"csrf_token" in response.data

def test_browser_flow_logs_exception_without_exposing_it(tmp_path, monkeypatch, caplog):
    cfg=Config(database_path=str(tmp_path/"browser-failure.sqlite"),test_ebay_browser_flow=True,
               ebay_browser_user_data_directory=str(tmp_path/"profile"))
    app=create_app(cfg); db=app.extensions["vintage_db"]
    item=db.create_item(title="Shown",description="D",price="1",currency="USD",quantity=1,
                        condition="Used",category="Clothes",photo_urls=[])
    client=app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"]="valid-test-token"

    def fail_browser_startup():
        raise RuntimeError("distinctive browser startup failure")

    monkeypatch.setattr(app_module,"async_playwright",fail_browser_startup)
    with caplog.at_level("ERROR"):
        response=client.post(f"/items/{item['id']}/test-ebay-browser-flow",
                             data={"csrf_token":"valid-test-token"})

    assert response.status_code==302
    assert "Test-only eBay browser flow failed" in caplog.text
    assert "RuntimeError: distinctive browser startup failure" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text

    detail=client.get(response.headers["Location"])
    assert detail.status_code==200
    assert b"browser_flow_failed" in detail.data
    assert b"distinctive browser startup failure" not in detail.data
    assert b"Traceback (most recent call last)" not in detail.data

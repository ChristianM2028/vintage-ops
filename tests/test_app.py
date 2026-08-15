from cryptography.fernet import Fernet
from app import create_app
from config import Config

def test_item_page_and_sandbox_guard(tmp_path):
    cfg=Config(database_path=str(tmp_path/"app.sqlite"),encryption_key=Fernet.generate_key().decode())
    app=create_app(cfg); db=app.extensions["vintage_db"]
    item=db.create_item(sku="S",title="Test",description="D",price="1",currency="USD",quantity=1,condition="3000",brand="B",size="M",color="C",category="1",photo_urls=["https://x.test/i"])
    response=app.test_client().get(f"/items/{item['id']}"); assert response.status_code==200 and b"eBay lifecycle" in response.data
    assert create_app(Config(database_path=str(tmp_path/"prod.sqlite"),environment="production")).test_client().get("/sandbox").status_code==404

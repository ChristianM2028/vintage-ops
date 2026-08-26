import json
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
from app import create_app
from config import Config

class RecordingHTTP:
    class Response:
        def __init__(self, data=None, status=204):
            self.status_code=status; self.data=data or {}; self.headers={}
            self.content=json.dumps(self.data).encode() if data is not None else b""
        @property
        def ok(self): return self.status_code < 400
        def json(self): return self.data
    def __init__(self, responses=()): self.responses=list(responses); self.calls=[]
    def request(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs)); return self.responses.pop(0)
    def post(self, *args, **kwargs): raise AssertionError("unexpected OAuth request")

def csrf(client):
    with client.session_transaction() as session: session["csrf_token"]="test-csrf"
    return {"csrf_token":"test-csrf"}

def test_item_page_and_sandbox_guard(tmp_path):
    cfg=Config(database_path=str(tmp_path/"app.sqlite"),encryption_key=Fernet.generate_key().decode())
    app=create_app(cfg); db=app.extensions["vintage_db"]
    item=db.create_item(sku="S",title="Test",description="D",price="1",currency="USD",quantity=1,condition="3000",brand="B",size="M",color="C",category="1",photo_urls=["https://x.test/i"])
    response=app.test_client().get(f"/items/{item['id']}"); assert response.status_code==200 and b"eBay lifecycle" in response.data
    assert create_app(Config(database_path=str(tmp_path/"prod.sqlite"),ebay_provider_mode="mock")).test_client().get("/sandbox").status_code==404

def test_routes_resolve_persisted_real_snapshot_and_local_mock(tmp_path):
    key=Fernet.generate_key().decode()
    cfg=Config(database_path=str(tmp_path/"modes.sqlite"),encryption_key=key,environment="sandbox",
        ebay_provider_mode="sandbox",location_key="GLOBAL-CHANGED",fulfillment_policy_id="global-f",
        payment_policy_id="global-p",return_policy_id="global-r",marketplace_id="EBAY_GB")
    http=RecordingHTTP([RecordingHTTP.Response(),RecordingHTTP.Response()])
    app=create_app(cfg,http=http); db=app.extensions["vintage_db"]
    fernet=Fernet(key.encode()); future=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    with db.transaction() as con:
        con.execute("INSERT INTO ebay_tokens VALUES (1,?,?,?,?,?,datetime('now'))",
            (fernet.encrypt(b"refresh"),fernet.encrypt(b"access"),future,future,
             "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.account https://api.ebay.com/oauth/api_scope/sell.fulfillment"))
    common=dict(title="Coat",description="D",price="10",currency="USD",quantity=1,
                condition="USED_EXCELLENT",brand="B",size="L",color="Blue",category="123",photo_urls=["https://x.test/a.jpg"])
    real=db.create_item(**common,ebay_integration_mode="ebay_sandbox",ebay_merchant_location_key="original-loc",
        ebay_fulfillment_policy_id="original-f",ebay_payment_policy_id="original-p",ebay_return_policy_id="original-r",
        ebay_marketplace_id="EBAY_US")
    real=db.update_item(real["id"],ebay_sku="REAL-SKU",ebay_offer_id="REAL-OFFER",ebay_listing_id="REAL-LIST",ebay_publication_state="published")
    client=app.test_client(); response=client.post(f"/items/{real['id']}/update-ebay",data=csrf(client))
    assert response.status_code==302 and len(http.calls)==2
    inventory=http.calls[0]; offer=http.calls[1]
    assert inventory[0]=="PUT" and inventory[1].endswith("/inventory_item/REAL-SKU")
    assert inventory[2]["json"]["product"]["aspects"]["Size"]==["L"]
    assert offer[2]["json"]["marketplaceId"]=="EBAY_US"
    assert offer[2]["json"]["merchantLocationKey"]=="original-loc"
    assert offer[2]["json"]["listingPolicies"]=={"fulfillmentPolicyId":"original-f","paymentPolicyId":"original-p","returnPolicyId":"original-r"}
    assert b"eBay Sandbox" in client.get(f"/items/{real['id']}").data

    no_http=RecordingHTTP([]); local_app=create_app(cfg,http=no_http); local_db=local_app.extensions["vintage_db"]
    local=local_db.create_item(**common,ebay_integration_mode="local_mock")
    local_client=local_app.test_client()
    for action in ("list-ebay","update-ebay","sync-ebay","delist-ebay"):
        assert local_client.post(f"/items/{local['id']}/{action}",data=csrf(local_client)).status_code==302
    assert no_http.calls==[] and b"Local mock" in local_client.get(f"/items/{local['id']}").data

def test_sandbox_creation_requires_valid_metadata_before_snapshot(tmp_path):
    cfg=Config(database_path=str(tmp_path/"sandbox-create.sqlite"),environment="sandbox",
               ebay_provider_mode="sandbox",location_key="changed-global")
    app=create_app(cfg); db=app.extensions["vintage_db"]
    db.save_seller_setup(None,"  fulfill  "," pay "," return "," location "," EBAY_US ")
    client=app.test_client()
    response=client.post("/sandbox",data={**csrf(client),"title":"Coat","description":"D",
        "price":"10","condition":"USED_EXCELLENT","category":"123","category_name":"",
        "photo_url":"https://example.test/coat.jpg"})
    assert response.status_code==200
    assert db.inventory()==[]
    assert b"Select a category from the suggestions" in response.data

def test_sandbox_creation_fails_closed_for_missing_or_incomplete_setup(tmp_path):
    app=create_app(Config(database_path=str(tmp_path/"sandbox-closed.sqlite"),environment="sandbox",
                          ebay_provider_mode="sandbox"))
    db=app.extensions["vintage_db"]; client=app.test_client()
    data={**csrf(client),"title":"Coat","description":"D","price":"10","condition":"USED_EXCELLENT",
          "category":"123","category_name":"","photo_url":"https://example.test/coat.jpg"}
    assert client.post("/sandbox",data=data).status_code==200
    assert db.inventory()==[]
    db.save_seller_setup(None,"fulfill","   ","return","location","EBAY_US")
    assert client.post("/sandbox",data=data).status_code==200
    assert db.inventory()==[]

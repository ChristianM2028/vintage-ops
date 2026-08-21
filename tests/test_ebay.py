import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.fernet import Fernet
from config import Config
from db import Database
from integrations.ebay.errors import EbayError, parse_error
from integrations.ebay.client import EbayClient
from integrations.ebay.oauth import OAuthManager
from integrations.ebay.service import EbayService

class Response:
    def __init__(self,status=200,data=None,headers=None): self.status_code=status; self.data=data or {}; self.headers=headers or {}; self.content=json.dumps(self.data).encode() if data is not None else b""
    @property
    def ok(self): return self.status_code<400
    def json(self): return self.data
class HTTP:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def post(self,*a,**kw): self.calls.append((a,kw)); return self.responses.pop(0)
class RequestHTTP:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,*a,**kw): self.calls.append((a,kw)); return self.responses.pop(0)
class OAuth:
    def __init__(self,token): self.token=token
    def access_token(self): return self.token
class FakeClient:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs)); value=self.responses.pop(0)
        if isinstance(value,Exception): raise value
        return value

@pytest.fixture
def env(tmp_path):
    cfg=Config(database_path=str(tmp_path/"db.sqlite"),encryption_key=Fernet.generate_key().decode(),client_id="id",client_secret="secret",runame="RuName",location_key="loc",fulfillment_policy_id="f",payment_policy_id="p",return_policy_id="r",category_id="123")
    db=Database(cfg.database_path); db.migrate(); return cfg,db
@pytest.fixture
def item(env):
    cfg,db=env; return db.create_item(sku="TEST-1",title="Jacket",description="Fake",price="10.00",currency="USD",quantity=1,condition="USED_EXCELLENT",brand="Brand",size="M",color="Blue",category="123",photo_urls=["https://example.test/photo.jpg"])

def test_state_is_one_time_and_validated(env):
    cfg,db=env; oauth=OAuthManager(cfg,db,HTTP([])); _,state=oauth.begin(); oauth.validate_state(state)
    with pytest.raises(EbayError): oauth.validate_state(state)
    with pytest.raises(EbayError): oauth.validate_state("wrong")
def test_authorization_code_exchange_is_encrypted(env):
    cfg,db=env; http=HTTP([Response(data={"access_token":"access","refresh_token":"refresh","expires_in":7200})]); OAuthManager(cfg,db,http).exchange_code("code")
    with db.connect() as c: row=c.execute("select * from ebay_tokens").fetchone()
    assert b"refresh" not in row["refresh_token_encrypted"] and http.calls[0][1]["data"]["grant_type"]=="authorization_code"
def test_automatic_refresh_and_revocation(env):
    cfg,db=env; first=OAuthManager(cfg,db,HTTP([Response(data={"access_token":"old","refresh_token":"refresh","expires_in":0})])); first.exchange_code("code")
    oauth=OAuthManager(cfg,db,HTTP([Response(data={"access_token":"new","expires_in":7200})])); assert oauth.access_token()=="new"
    db.update_item if False else None
    with db.transaction() as c: c.execute("update ebay_tokens set access_expires_at=?",((datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),))
    with pytest.raises(EbayError) as exc: OAuthManager(cfg,db,HTTP([Response(400,{"errors":[{"errorId":1100,"message":"invalid refresh"}]})])).access_token()
    assert exc.value.kind=="reauthorization_required"
def test_full_lifecycle_persists_ids_and_withdraws(env,item):
    cfg,db=env; client=FakeClient([{}, {"offerId":"OFF1"},{"listingId":"LIST1"}, {}, {}, {}]); svc=EbayService(cfg,db,client)
    listed=svc.listItem(item); assert (listed["ebay_sku"],listed["ebay_offer_id"],listed["ebay_listing_id"],listed["ebay_publication_state"])==("TEST-1","OFF1","LIST1","published")
    svc.updateItem(listed); withdrawn=svc.delistItem(db.get_item(item["id"])); assert withdrawn["ebay_publication_state"]=="withdrawn"
    assert [c[0][0] for c in client.calls]==["PUT","POST","POST","PUT","PUT","POST"]
def test_partial_publish_retry_reuses_offer(env,item):
    cfg,db=env; failure=EbayError("remote","publish failed")
    with pytest.raises(EbayError) as exc: EbayService(cfg,db,FakeClient([{}, {"offerId":"OFF1"},failure])).listItem(item)
    partial=db.get_item(item["id"]); assert partial["ebay_offer_id"]=="OFF1" and partial["ebay_publication_state"]=="partial_failure" and exc.value.kind=="partial_publication"
    client=FakeClient([{}, {"listingId":"LIST1"}]); done=EbayService(cfg,db,client).listItem(partial); assert done["listing_id"] if False else done["ebay_listing_id"]=="LIST1"; assert len(client.calls)==2
def test_prerequisite_and_photo_validation(env,item):
    cfg,db=env
    with pytest.raises(EbayError) as exc: EbayService(replace(cfg,location_key=""),db,FakeClient([])).listItem(item)
    assert exc.value.kind=="missing_prerequisite"
    bad=dict(item,photo_urls='["http://private/image"]')
    with pytest.raises(EbayError) as exc: EbayService(cfg,db,FakeClient([])).listItem(bad)
    assert exc.value.kind=="invalid_item"
def test_structured_error():
    error=parse_error(Response(409,{"errors":[{"errorId":25001,"domain":"API_INVENTORY","category":"REQUEST","message":"Conflict","parameters":[{"name":"offerId","value":"x"}]}]},{"X-EBAY-C-REQUEST-ID":"req","RLoGiD":"rlog-1"}))
    assert error.kind=="conflict" and error.details[0]["errorId"]==25001 and error.request_id=="req" and error.rlogid=="rlog-1"
    assert error.safe_dict()=={"kind":"conflict","message":"Conflict","status":409,
        "request_id":"req","rlogid":"rlog-1","details":error.details}

def test_response_logging_is_structured_correlated_and_secret_free(env,caplog):
    cfg,_=env
    token="SENTINEL-BEARER-CREDENTIAL"
    secret="SENTINEL-CLIENT-SECRET"
    cfg=replace(cfg,client_secret=secret)
    response=Response(500,{"errors":[{"errorId":25002,"message":"Remote failure"}]},{
        "RLOGID":"rlog-500","X-eBay-C-Request-Id":"request-500",
        "X-EBAY-CORRELATION-ID":"correlation-500"})
    http=RequestHTTP([response])
    client=EbayClient(cfg,OAuth(token),http)

    with caplog.at_level(logging.DEBUG,logger="integrations.ebay.client"):
        with pytest.raises(EbayError):
            client.request("post","/sell/inventory/v1/location/vintage-ops-main",json={"credential":secret})

    assert len(caplog.records)==1
    assert caplog.records[0].ebay_response=={
        "method":"POST","path":"/sell/inventory/v1/location/vintage-ops-main","status":500,
        "error_id":25002,"rlogid":"rlog-500","x-ebay-c-request-id":"request-500",
        "x-ebay-correlation-id":"correlation-500"}
    rendered=caplog.text+repr(caplog.records[0].ebay_response)
    assert token not in rendered and secret not in rendered
def test_withdraw_idempotent_and_failure(env,item):
    cfg,db=env; withdrawn=dict(item,ebay_offer_id="O",ebay_publication_state="withdrawn"); assert not EbayService(cfg,db,FakeClient([])).delistItem(withdrawn) is None
    with pytest.raises(EbayError) as exc: EbayService(cfg,db,FakeClient([EbayError("remote","no")])).delistItem(dict(item,ebay_offer_id="O"))
    assert exc.value.kind=="withdrawal_failure"

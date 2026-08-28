import io

from app import create_app
from config import Config
from integrations.ebay.metadata import EbayMetadataService
from integrations.ebay.photos import EbayImagePublisher
from integrations.ebay.service import EbayService


def form(token):
    return {
        "csrf_token": token, "title": "Sandbox coat", "description": "Vintage",
        "price": "24.00", "condition": "USED_EXCELLENT", "category": "123",
        "category_name": "Coats", "photos": (io.BytesIO(b"\x89PNG\r\n\x1a\nsafe"), "coat.png"),
    }


def token(client):
    page=client.get("/").text
    return page.split('name="csrf_token" value="',1)[1].split('"',1)[0]


def test_railway_sandbox_seller_creation_snapshots_then_publishes(tmp_path, monkeypatch):
    cfg=Config(database_path=str(tmp_path/"railway.sqlite"),upload_directory=str(tmp_path/"uploads"),
               ebay_provider_mode="sandbox",environment="sandbox")
    app=create_app(cfg); db=app.extensions["vintage_db"]
    db.save_seller_setup(None,"  fulfillment  "," payment "," return "," location "," EBAY_US ")
    monkeypatch.setattr(EbayMetadataService,"aspects",lambda self,category: {"aspects":[]})
    monkeypatch.setattr(EbayMetadataService,"conditions",lambda self,category: {
        "itemConditionPolicies":[{"itemConditions":[{"conditionId":3000,"conditionDescription":"Used - Excellent"}]}]})
    monkeypatch.setattr(EbayImagePublisher,"publish",lambda self,item: ["https://i.ebayimg.com/coat.png"])
    published=[]
    def publish(self,item):
        published.append(dict(item))
        return self.db.update_item(item["id"],ebay_sku=item["sku"],ebay_offer_id="offer-1",
                                   ebay_listing_id="listing-1",ebay_publication_state="published")
    monkeypatch.setattr(EbayService,"list",publish)

    client=app.test_client()
    response=client.post("/",data=form(token(client)),content_type="multipart/form-data")
    assert response.status_code==302
    item=db.inventory()[0]
    assert item["ebay_integration_mode"]=="ebay_sandbox"
    assert {key:item[key] for key in ("ebay_merchant_location_key","ebay_fulfillment_policy_id",
        "ebay_payment_policy_id","ebay_return_policy_id","ebay_marketplace_id")}=={
        "ebay_merchant_location_key":"location","ebay_fulfillment_policy_id":"fulfillment",
        "ebay_payment_policy_id":"payment","ebay_return_policy_id":"return","ebay_marketplace_id":"EBAY_US"}
    assert published and published[0]["ebay_merchant_location_key"]=="location"
    assert item["ebay_publication_state"]=="published" and item["ebay_listing_id"]=="listing-1"


def test_seller_creation_rejects_incomplete_snapshot_without_item_or_upload(tmp_path, monkeypatch):
    cfg=Config(database_path=str(tmp_path/"closed.sqlite"),upload_directory=str(tmp_path/"uploads"),
               ebay_provider_mode="sandbox",environment="sandbox")
    app=create_app(cfg); db=app.extensions["vintage_db"]
    db.save_seller_setup(None,"fulfillment"," ","return","location","EBAY_US")
    monkeypatch.setattr(EbayMetadataService,"aspects",lambda self,category: {"aspects":[]})
    monkeypatch.setattr(EbayMetadataService,"conditions",lambda self,category: {
        "itemConditionPolicies":[{"itemConditions":[{"conditionId":3000}]}]})
    client=app.test_client()
    response=client.post("/",data=form(token(client)),content_type="multipart/form-data")
    assert response.status_code==200
    assert b"seller configuration is incomplete" in response.data
    assert db.inventory()==[] and list((tmp_path/"uploads").iterdir())==[]

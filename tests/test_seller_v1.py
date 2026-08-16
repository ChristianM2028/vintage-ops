import io
from concurrent.futures import ThreadPoolExecutor
from config import Config
from db import Database
from integrations.ebay.mock import MockEbayAdapter


def values(title="Jacket"):
    return dict(title=title,description="Vintage garment",price="12.50",currency="USD",quantity=1,condition="Used",brand="Acme",size="M",color="Blue",category="Coats",photo_urls=[])


def test_sequential_concurrent_skus_and_photo_order(tmp_path):
    db=Database(str(tmp_path/"items.sqlite")); db.migrate()
    def create(n): return db.create_item(**values(str(n)))
    with ThreadPoolExecutor(max_workers=8) as pool: items=list(pool.map(create,range(20)))
    assert sorted(x["sku"] for x in items)==[f"VO-{n:06d}" for n in range(1,21)]
    item=db.create_item(**values(),photos=[{"filename":"a.jpg","original_filename":"first.jpg"},{"filename":"b.png","original_filename":"second.png"}])
    assert [(p["original_filename"],p["display_order"]) for p in db.get_photos(item["id"])]==[("first.jpg",0),("second.png",1)]


def test_mock_lifecycle_is_idempotent_without_http_or_oauth(tmp_path):
    db=Database(str(tmp_path/"items.sqlite")); db.migrate(); item=db.create_item(**values())
    adapter=MockEbayAdapter(Config(database_path=db.path),db)
    listed=adapter.list(item); again=adapter.list(listed)
    assert listed["ebay_offer_id"].startswith("MOCK-OFFER-") and again["ebay_listing_id"]==listed["ebay_listing_id"]
    withdrawn=adapter.delist(again); assert adapter.delist(withdrawn)["ebay_publication_state"]=="withdrawn" and withdrawn["status"]=="Delisted"


def csrf(client):
    page=client.get("/").text
    return page.split('name="csrf_token" value="',1)[1].split('"',1)[0]


def test_create_validation_photos_inventory_and_actions(tmp_path):
    from app import create_app
    cfg=Config(database_path=str(tmp_path/"app.sqlite"),upload_directory=str(tmp_path/"uploads"),marketplace_mock_mode=True)
    app=create_app(cfg); app.testing=True; client=app.test_client()
    token=csrf(client); bad=client.post("/",data={"csrf_token":token,"title":"Remember me"},content_type="multipart/form-data")
    assert bad.status_code==200 and b"Remember me" in bad.data and b"Price is required" in bad.data
    png=b"\x89PNG\r\n\x1a\n"+b"safe"
    data={"csrf_token":token,"title":"Jacket","description":"Nice","price":"20.00","condition":"Used","category":"Coats","brand":"B","size":"M","color":"Red","photos":[(io.BytesIO(png),"one.png"),(io.BytesIO(png),"two.png")]}
    made=client.post("/",data=data,content_type="multipart/form-data",follow_redirects=True)
    assert made.status_code==200 and b"VO-000001" in made.data
    inventory=client.get("/inventory"); assert b"Jacket" in inventory.data and b"Draft" in inventory.data
    item=app.extensions["vintage_db"].inventory()[0]
    listed=client.post(f"/items/{item['id']}/list-everywhere",data={"csrf_token":token},follow_redirects=True)
    refreshed=app.extensions["vintage_db"].get_item(item["id"])
    assert b"Listed on every configured marketplace (eBay)" in listed.data and refreshed["status"]=="Active" and "depop" not in str(refreshed).lower()
    sold=client.post(f"/items/{item['id']}/sold-delist-everywhere",data={"csrf_token":token})
    assert sold.status_code==302 and app.extensions["vintage_db"].get_item(item["id"])["status"]=="Sold"


def test_mock_mode_can_be_disabled(tmp_path):
    from app import create_app
    app=create_app(Config(database_path=str(tmp_path/"real.sqlite"),marketplace_mock_mode=False))
    from integrations.ebay.service import EbayService
    assert isinstance(app.extensions["marketplace_adapters"]()["ebay"],EbayService)


def test_storefront_catalog_detail_filters_and_safe_projection(tmp_path):
    from app import create_app
    app=create_app(Config(database_path=str(tmp_path/"shop.sqlite"),upload_directory=str(tmp_path/"uploads")))
    app.testing=True; db=app.extensions["vintage_db"]; client=app.test_client()
    active=db.create_item(**values("Indigo Chore Jacket")); db.update_item(active["id"],status="Active",storefront_visible=1)
    cheap=db.create_item(**{**values("Canvas Shirt"),"description":"workwear staple","price":"8.00","brand":"Acme","size":"M"}); db.update_item(cheap["id"],status="Active",storefront_visible=1)
    hidden=db.create_item(**values("Hidden Coat")); db.update_item(hidden["id"],status="Active")
    sold=db.create_item(**values("Sold Treasure")); db.update_item(sold["id"],status="Sold",storefront_visible=1)

    catalog=client.get("/shop")
    assert catalog.status_code==200 and b"Indigo Chore Jacket" in catalog.data and b"Canvas Shirt" in catalog.data
    assert b"Hidden Coat" not in catalog.data and b"Sold Treasure" not in catalog.data
    filtered=client.get("/shop?q=workwear&brand=acme&size=m&min_price=5&max_price=10&sort=price_desc")
    assert b"Canvas Shirt" in filtered.data and b"Indigo Chore Jacket" not in filtered.data
    public=db.get_storefront_item_by_sku(active["sku"])
    assert "id" not in public and all("id" not in photo for photo in public["photos"])

    detail=client.get(f"/shop/item/{active['sku']}")
    assert detail.status_code==200 and b"Indigo Chore Jacket" in detail.data and b"eBay lifecycle" not in detail.data
    sold_detail=client.get(f"/shop/item/{sold['sku']}")
    assert sold_detail.status_code==200 and b"Unavailable" in sold_detail.data
    assert client.get(f"/shop/item/{hidden['sku']}").status_code==404
    assert client.get("/shop/item/DOES-NOT-EXIST").status_code==404


def test_storefront_validation_sorting_and_visibility_csrf(tmp_path):
    from app import create_app
    app=create_app(Config(database_path=str(tmp_path/"shop.sqlite"),upload_directory=str(tmp_path/"uploads")))
    app.testing=True; db=app.extensions["vintage_db"]; client=app.test_client()
    high=db.create_item(**{**values("High"),"price":"20.00"}); low=db.create_item(**{**values("Low"),"price":"5.00"})
    for item in (high,low): db.update_item(item["id"],status="Active",storefront_visible=1)
    response=client.get("/shop?sort=price_asc")
    assert response.data.index(b"Low") < response.data.index(b"High")
    for query in ("sort=bogus","min_price=-1","max_price=nope","min_price=9&max_price=2","unknown=x"):
        assert client.get("/shop?"+query).status_code==400

    assert client.post(f"/items/{high['id']}/storefront-visibility",data={"storefront_visible":"0"}).status_code==400
    token=csrf(client)
    changed=client.post(f"/items/{high['id']}/storefront-visibility",data={"csrf_token":token,"storefront_visible":"0"})
    assert changed.status_code==302 and changed.headers["Location"].endswith(f"/items/{high['id']}")
    assert db.get_item(high["id"])["storefront_visible"]==0

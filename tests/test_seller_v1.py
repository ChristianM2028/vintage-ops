import io
from concurrent.futures import ThreadPoolExecutor
from config import Config
from db import Database
from integrations.ebay.mock import MockEbayAdapter
from werkzeug.security import generate_password_hash


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


def listing_form(token, title="Jacket"):
    png=b"\x89PNG\r\n\x1a\n"+b"safe"
    return {"csrf_token":token,"title":title,"description":"Nice","price":"20.00",
            "condition":"Used","category":"Coats","brand":"B","size":"M","color":"Red",
            "photos":[(io.BytesIO(png),"one.png"),(io.BytesIO(png),"two.png")]}


def test_create_validation_photos_inventory_and_actions(tmp_path):
    from app import create_app
    cfg=Config(database_path=str(tmp_path/"app.sqlite"),upload_directory=str(tmp_path/"uploads"),ebay_provider_mode="mock")
    app=create_app(cfg); app.testing=True; client=app.test_client()
    token=csrf(client); bad=client.post("/",data={"csrf_token":token,"title":"Remember me"},content_type="multipart/form-data")
    assert bad.status_code==200 and b"Remember me" in bad.data and b"Price is required" in bad.data
    made=client.post("/",data=listing_form(token),content_type="multipart/form-data",follow_redirects=True)
    assert made.status_code==200 and b"VO-000001" in made.data and b"Storefront: Live" in made.data and b"eBay: Published" in made.data
    inventory=client.get("/inventory"); assert b"Jacket" in inventory.data and b"Active" in inventory.data
    item=app.extensions["vintage_db"].inventory()[0]
    assert item["storefront_visible"]==1 and item["ebay_publication_state"]=="published"
    listed=client.post(f"/items/{item['id']}/list-everywhere",data={"csrf_token":token},follow_redirects=True)
    refreshed=app.extensions["vintage_db"].get_item(item["id"])
    assert b"Listed on every configured marketplace (eBay)" in listed.data and refreshed["status"]=="Active" and "depop" not in str(refreshed).lower()
    sold=client.post(f"/items/{item['id']}/sold-delist-everywhere",data={"csrf_token":token})
    assert sold.status_code==302 and app.extensions["vintage_db"].get_item(item["id"])["status"]=="Sold"


def test_list_item_keeps_storefront_live_when_ebay_fails(tmp_path,monkeypatch):
    from app import create_app
    from integrations.ebay.errors import EbayError
    def unavailable(self,item): raise EbayError("remote","eBay is unavailable")
    monkeypatch.setattr(MockEbayAdapter,"list",unavailable)
    app=create_app(Config(database_path=str(tmp_path/"failure.sqlite"),upload_directory=str(tmp_path/"uploads"),ebay_provider_mode="mock"))
    app.testing=True; client=app.test_client(); token=csrf(client)
    response=client.post("/",data=listing_form(token),content_type="multipart/form-data",follow_redirects=True)
    item=app.extensions["vintage_db"].inventory()[0]
    assert response.status_code==200 and b"Storefront: Live" in response.data and b"eBay: Needs attention" in response.data
    assert item["status"]=="Active" and item["storefront_visible"]==1 and item["ebay_listing_id"] is None
    assert b"Jacket" in client.get("/shop").data


def test_retry_after_ambiguous_success_reuses_ebay_offer(tmp_path,monkeypatch):
    from app import create_app
    from integrations.ebay.errors import EbayError
    original=MockEbayAdapter.list; calls=[]
    def ambiguous_once(self,item):
        listed=original(self,item); calls.append(listed["ebay_offer_id"])
        if len(calls)==1: raise EbayError("remote","response was lost")
        return listed
    monkeypatch.setattr(MockEbayAdapter,"list",ambiguous_once)
    app=create_app(Config(database_path=str(tmp_path/"retry.sqlite"),upload_directory=str(tmp_path/"uploads"),ebay_provider_mode="mock"))
    app.testing=True; client=app.test_client(); token=csrf(client)
    client.post("/",data=listing_form(token),content_type="multipart/form-data")
    item=app.extensions["vintage_db"].inventory()[0]; first_offer=item["ebay_offer_id"]
    response=client.post(f"/items/{item['id']}/list-ebay",data={"csrf_token":token},follow_redirects=True)
    retried=app.extensions["vintage_db"].get_item(item["id"])
    assert response.status_code==200 and b"Listed on eBay" in response.data
    assert calls==[first_offer,first_offer] and retried["ebay_offer_id"]==first_offer and retried["ebay_publication_state"]=="published"


def test_mock_mode_can_be_disabled(tmp_path):
    from app import create_app
    app=create_app(Config(database_path=str(tmp_path/"real.sqlite"),ebay_provider_mode="sandbox"))
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


def test_seller_navigation_and_post_only_logout(tmp_path):
    from app import create_app
    cfg=Config(
        database_path=str(tmp_path/"auth.sqlite"),
        upload_directory=str(tmp_path/"uploads"),
        seller_username="seller",
        seller_password_hash=generate_password_hash("correct horse battery staple"),
    )
    app=create_app(cfg); app.testing=True; client=app.test_client()

    storefront=client.get("/shop")
    assert b'href="/login"' in storefront.data and b"Admin sign in" in storefront.data
    assert b"Admin dashboard" not in storefront.data

    login=client.get("/login")
    token=login.text.split('name="csrf_token" value="',1)[1].split('"',1)[0]
    signed_in=client.post("/login",data={
        "csrf_token":token,
        "username":"seller",
        "password":"correct horse battery staple",
    })
    assert signed_in.status_code==302 and signed_in.headers["Location"].endswith("/")

    authenticated_storefront=client.get("/shop")
    assert b'href="/"' in authenticated_storefront.data and b"Admin dashboard" in authenticated_storefront.data
    assert b"Admin sign in" not in authenticated_storefront.data

    admin=client.get("/")
    assert b'href="/shop"' in admin.data and b"View storefront" in admin.data
    assert b'method="post" action="/logout"' in admin.data and b"Log out" in admin.data
    logout_token=admin.text.split('action="/logout"',1)[1].split('name="csrf_token" value="',1)[1].split('"',1)[0]

    assert client.get("/logout").status_code==405
    assert client.post("/logout").status_code==400
    logged_out=client.post("/logout",data={"csrf_token":logout_token})
    assert logged_out.status_code==302 and logged_out.headers["Location"].endswith("/login")
    with client.session_transaction() as session:
        assert "seller_authenticated" not in session
    assert client.get("/").headers["Location"].startswith("/login?")

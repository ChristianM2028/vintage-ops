from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app import create_app
from config import Config


def csrf(client):
    with client.session_transaction() as session:
        session["csrf_token"] = "storefront-test"
    return {"csrf_token": "storefront-test"}


def setup_store(tmp_path, **overrides):
    cfg = replace(Config(), database_path=str(tmp_path / "store.sqlite"),
                  stripe_fake_mode=True, environment="sandbox", **overrides)
    app = create_app(cfg)
    db = app.extensions["vintage_db"]
    item = db.create_item(sku="TEST-COAT", title="Test Coat", description="A coat",
                          price="45.00", currency="USD", quantity=1,
                          condition="Used", brand="Vintage", size="M", color="Blue",
                          category="1", category_name="Coats")
    db.update_item(item["id"], status="Active", storefront_visible=1)
    return app, db, item


def start_checkout(app, sku="TEST-COAT"):
    client = app.test_client()
    response = client.post(f"/shop/item/{sku}/checkout", data=csrf(client))
    assert response.status_code == 303
    public_id = response.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    return client, public_id


def reservation(db, public_id):
    with db.connect() as con:
        return dict(con.execute("""SELECT r.* FROM item_reservations r
            JOIN storefront_orders o ON o.id=r.order_id WHERE o.public_id=?""",
            (public_id,)).fetchone())


def test_reservation_stays_visible_blocks_duplicate_and_cancel_restores(tmp_path):
    app, db, _ = setup_store(tmp_path)
    assert db.storefront_catalog()[0]["availability"] == "available"
    assert b"Test Coat" in app.test_client().get("/shop").data

    first, public_id = start_checkout(app)
    assert reservation(db, public_id)["state"] == "open"
    assert db.storefront_catalog()[0]["availability"] == "reserved"
    for path in ("/shop", "/shop/item/TEST-COAT"):
        page = first.get(path)
        assert b"Checkout in progress" in page.data
        assert b"disabled" in page.data

    second = app.test_client()
    assert second.post("/shop/item/TEST-COAT/checkout", data=csrf(second)).status_code == 409
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM storefront_orders").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM item_reservations").fetchone()[0] == 1

    # All GET destinations are observational only.
    before = (db.get_order(public_id)["state"], reservation(db, public_id)["state"])
    for suffix in ("", "/success", "/cancel"):
        assert first.get(f"/shop/order/{public_id}{suffix}").status_code == 200
    assert (db.get_order(public_id)["state"], reservation(db, public_id)["state"]) == before

    assert first.post(f"/shop/order/{public_id}/cancel-checkout", data=csrf(first)).status_code == 303
    assert db.get_order(public_id)["state"] == "cancelled"
    assert reservation(db, public_id)["state"] == "released"
    assert db.storefront_catalog()[0]["availability"] == "available"
    assert db.get_storefront_item_by_sku("TEST-COAT")["availability"] == "available"
    assert first.post(f"/shop/order/{public_id}/cancel-checkout", data=csrf(first)).status_code == 303
    assert first.post(f"/shop/order/{public_id}/complete", data=csrf(first)).status_code == 409


def test_expiration_and_fake_completion_use_order_state_machine(tmp_path):
    app, db, item = setup_store(tmp_path)
    client, expired_id = start_checkout(app)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.transaction() as con:
        con.execute("UPDATE item_reservations SET expires_at=? WHERE order_id=(SELECT id FROM storefront_orders WHERE public_id=?)", (past, expired_id))
    assert db.get_storefront_item_by_sku("TEST-COAT")["availability"] == "available"
    assert db.expire_reservations() == 1
    assert db.get_order(expired_id)["state"] == "expired"
    assert reservation(db, expired_id)["state"] == "expired"

    client, public_id = start_checkout(app)
    assert client.post(f"/shop/order/{public_id}/complete", data=csrf(client)).status_code == 303
    assert db.get_order(public_id)["state"] == "sold"
    sold_reservation = reservation(db, public_id)
    assert sold_reservation["state"] == "closed" and sold_reservation["close_reason"] == "sold"
    sold_item = db.get_item(item["id"])
    assert sold_item["quantity"] == 0 and sold_item["status"] == "Sold"
    assert db.storefront_catalog() == []
    assert b"Test Coat" not in client.get("/shop").data
    assert client.post(f"/shop/order/{public_id}/complete", data=csrf(client)).status_code == 303
    assert client.post(f"/shop/order/{public_id}/cancel-checkout", data=csrf(client)).status_code == 409


def test_fake_actions_are_disabled_in_real_or_production_mode(tmp_path):
    for name, overrides in (("real", {"stripe_fake_mode": False}),
                            ("production", {"environment": "production"})):
        cfg = replace(Config(), database_path=str(tmp_path / f"{name}.sqlite"), **overrides)
        app = create_app(cfg)
        client = app.test_client()
        for action in ("complete", "cancel-checkout"):
            assert client.post(f"/shop/order/missing/{action}", data=csrf(client)).status_code == 404

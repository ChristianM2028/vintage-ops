"""Shared high-fidelity storefront order fixtures."""
from dataclasses import replace

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from config import Config


@pytest.fixture
def order_factory(tmp_path):
    counter = 0

    def make(*, sold=True, authenticated=True):
        nonlocal counter
        counter += 1
        cfg = replace(
            Config(), database_path=str(tmp_path / f"orders-{counter}.sqlite"),
            stripe_provider_mode="fake", environment="sandbox",
            seller_username="seller" if authenticated else "",
            seller_password_hash=(generate_password_hash("secret-password")
                                  if authenticated else ""),
        )
        app = create_app(cfg)
        db = app.extensions["vintage_db"]
        sku = f"ORDER-{counter}"
        item = db.create_item(
            sku=sku, title="Private vintage coat", description="A coat",
            price="42.50", currency="USD", quantity=1, condition="Used",
            brand="Vintage", size="M", color="Blue", category="1",
            category_name="Coats",
        )
        db.update_item(item["id"], status="Active", storefront_visible=1)
        shopper = app.test_client()
        with shopper.session_transaction() as session:
            session["csrf_token"] = "shopper-token"
        response = shopper.post(f"/shop/item/{sku}/checkout",
                                data={"csrf_token": "shopper-token"})
        public_id = response.headers["Location"].rsplit("/", 1)[-1]
        if sold:
            assert shopper.post(f"/shop/order/{public_id}/complete",
                                data={"csrf_token": "shopper-token"}).status_code == 303
        return app, db, shopper, public_id, item

    return make


@pytest.fixture
def seller_login():
    def login(app):
        client = app.test_client()
        page = client.get("/login").text
        token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        response = client.post("/login", data={
            "csrf_token": token, "username": "seller", "password": "secret-password",
        })
        assert response.status_code == 302
        with client.session_transaction() as session:
            return client, session["csrf_token"]
    return login

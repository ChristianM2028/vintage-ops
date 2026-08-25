"""Regression contract for the seller-fulfillment schema migration."""
from pathlib import Path

from db import Database


ROOT = Path(__file__).parents[1]


def apply_through(con, final):
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        if int(path.name.split("_", 1)[0]) > final:
            break
        con.executescript(path.read_text())


def test_migration_backfills_only_historical_sold_captured_and_preserves_graph(tmp_path):
    path = tmp_path / "historical.sqlite"
    db = Database(str(path))
    with db.connect() as con:
        apply_through(con, 11)
        stamp = "2026-01-01T00:00:00+00:00"
        con.execute("INSERT INTO items(sku,title,price,currency,quantity,condition,category,status,created_at,updated_at) VALUES('MIG','Coat','10','USD',0,'Used','1','Sold',?,?)", (stamp, stamp))
        item_id = con.execute("SELECT id FROM items WHERE sku='MIG'").fetchone()[0]
        states = [("sold", "captured"), ("paid", "captured"),
                  ("manual_resolution", "authorized"), ("cancelled", "cancelled")]
        for index, (state, payment) in enumerate(states):
            con.execute("""INSERT INTO storefront_orders(public_id,item_id,state,payment_state,
              price_snapshot,currency_snapshot,stripe_provider_identity,created_at,updated_at)
              VALUES(?,?,?,?,10,'USD','fake',?,?)""",
              (f"history-{index}", item_id, state, payment, stamp, stamp))
        before = [tuple(row) for row in con.execute("SELECT id,public_id,item_id,state,payment_state FROM storefront_orders ORDER BY id")]
        con.executescript((ROOT / "migrations/012_seller_fulfillment.sql").read_text())
        after = [tuple(row) for row in con.execute("SELECT id,public_id,item_id,state,payment_state FROM storefront_orders ORDER BY id")]
        fulfillment = [row[0] for row in con.execute("SELECT fulfillment_state FROM storefront_orders ORDER BY id")]
    assert before == after
    assert fulfillment == ["unfulfilled", "not_applicable", "not_applicable", "not_applicable"]


def test_database_migration_is_repeat_safe_and_final_sale_preserves_advanced_state(tmp_path):
    db = Database(str(tmp_path / "repeat.sqlite"))
    db.migrate(); db.migrate()
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='012_seller_fulfillment.sql'").fetchone()[0] == 1

    item = db.create_item(sku="ADVANCED", title="Coat", description="", price="10",
                          currency="USD", quantity=1, condition="Used", brand="", size="",
                          color="", category="1", category_name="Coats")
    db.update_item(item["id"], status="Active", storefront_visible=1)
    order = db.create_storefront_order("ADVANCED", provider_identity="fake")
    with db.transaction() as con:
        con.execute("UPDATE storefront_orders SET state='paid',payment_state='captured',fulfillment_state='packing' WHERE public_id=?", (order["public_id"],))
    assert db.finalize_storefront_sale(order["public_id"])["fulfillment_state"] == "packing"

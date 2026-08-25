"""Regression coverage for guarded Sandbox seller snapshot backfill."""
import json
from pathlib import Path

from db import Database

ROOT=Path(__file__).parents[1]

def seed(path):
    db=Database(str(path))
    with db.connect() as con:
        for migration in sorted((ROOT/"migrations").glob("*.sql")):
            if migration.name.startswith("015_"): break
            con.executescript(migration.read_text())
    return db

def add_item(db,sku,**values):
    base=dict(title="Coat",description="",price="10",currency="USD",quantity=1,
              condition="Used",category="1",category_name="Coats",ebay_integration_mode="ebay_sandbox")
    deferred={key:values.pop(key) for key in tuple(values) if key in
              {"last_error","ebay_offer_id","ebay_listing_id","ebay_publication_state"}}
    item=db.create_item(sku=sku,**base,**values)
    return db.update_item(item["id"],**deferred) if deferred else item

def run(db):
    with db.connect() as con: con.executescript((ROOT/"migrations/015_sandbox_item_seller_snapshots.sql").read_text())

def setup(db, values=("  F  "," P "," R "," LOC "," EBAY_US ")):
    db.save_seller_setup(None,*values)

def test_migration_backfills_vo_rows_preserving_nonblank_and_clears_only_obsolete_error(tmp_path):
    db=seed(tmp_path/"migration.sqlite"); setup(db)
    obsolete=json.dumps({"code":"classification_required","message":"This item is missing its persisted eBay seller configuration"})
    one=add_item(db,"VO-000001",ebay_merchant_location_key=" custom ",ebay_fulfillment_policy_id=" ",last_error=obsolete)
    two=add_item(db,"VO-000002",ebay_payment_policy_id="KEEP",last_error="another error")
    run(db); one=db.get_item(one["id"]); two=db.get_item(two["id"])
    assert (one["ebay_merchant_location_key"],one["ebay_fulfillment_policy_id"],one["last_error"])==(" custom ","F",None)
    assert two["ebay_payment_policy_id"]=="KEEP" and two["ebay_merchant_location_key"]=="LOC"
    assert two["last_error"]=="another error"

def test_migration_is_noop_without_single_complete_setup(tmp_path):
    for index,values in enumerate((None,("F"," ","R","LOC","EBAY_US"))):
        db=seed(tmp_path/f"noop-{index}.sqlite")
        if values: setup(db,values)
        item=add_item(db,"VO-000001",ebay_merchant_location_key=" ")
        before=dict(db.get_item(item["id"])); run(db)
        assert dict(db.get_item(item["id"]))==before

def test_migration_never_changes_offered_listed_or_published_rows(tmp_path):
    db=seed(tmp_path/"associated.sqlite"); setup(db)
    rows=[add_item(db,"VO-000001",ebay_offer_id="offer"),
          add_item(db,"VO-000002",ebay_listing_id="listing"),
          add_item(db,"VO-000003",ebay_publication_state="published")]
    before=[]
    with db.connect() as con:
        for item in rows: before.append(bytes(con.execute("SELECT quote(json_object('id',id,'sku',sku,'offer',ebay_offer_id,'listing',ebay_listing_id,'publication',ebay_publication_state,'location',ebay_merchant_location_key,'error',last_error)) FROM items WHERE id=?",(item["id"],)).fetchone()[0],"utf8"))
    run(db)
    with db.connect() as con:
        after=[bytes(con.execute("SELECT quote(json_object('id',id,'sku',sku,'offer',ebay_offer_id,'listing',ebay_listing_id,'publication',ebay_publication_state,'location',ebay_merchant_location_key,'error',last_error)) FROM items WHERE id=?",(item["id"],)).fetchone()[0],"utf8") for item in rows]
    assert after==before

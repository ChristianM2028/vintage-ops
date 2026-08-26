"""Network-free contract tests for the live-commerce safety boundary."""
import sqlite3
from types import SimpleNamespace
import pytest
from db import Database
from integrations.ebay.errors import EbayError
from integrations.ebay.production_validation import ProductionSellerValidator, require_production_activation


def test_money_snapshots_are_immutable(tmp_path):
    db=Database(str(tmp_path/'db.sqlite')); db.migrate()
    with db.connect() as c:
        # Foreign-key prerequisites are intentionally minimal; this proves both
        # the snapshot columns and the database-level (not ORM-only) guard.
        c.execute("INSERT INTO items(sku,title,description,price,currency,quantity,status,condition,category,photo_urls,item_specifics,created_at,updated_at) VALUES('S','T','D','10.00','USD',1,'Active','USED','1','[]','{}','x','x')")
        c.execute("INSERT INTO storefront_orders(public_id,item_id,price_snapshot,currency_snapshot,item_subtotal_amount,shipping_amount,tax_amount,final_total_amount,state,created_at,updated_at) VALUES('O',1,'10.00','USD',1000,0,0,1000,'checkout_pending','x','x')")
        with pytest.raises(sqlite3.IntegrityError,match="immutable"):
            c.execute("UPDATE storefront_orders SET final_total_amount=999 WHERE public_id='O'")

class Client:
    def request(self,method,path):
        assert method=='GET'
        if path.endswith('/user/'): return {'username':'seller'}
        if '/location/' in path: return {'merchantLocationStatus':'ENABLED'}
        if '_policy/' in path: return {'marketplaceId':'EBAY_US'}
        if path.endswith('/privilege'): return {'sellingLimit':{'amount':{'currency':'USD'}}}
        raise AssertionError(path)

def cfg(**changes):
    values=dict(ebay_provider_mode='production',environment='production',expected_ebay_seller_id='seller',production_provider_activation_id='activation',marketplace_id='EBAY_US',location_key='loc',fulfillment_policy_id='f',payment_policy_id='p',return_policy_id='r',currency='USD')
    values.update(changes); return SimpleNamespace(**values)

def test_production_validation_is_read_only_and_identity_bound(tmp_path):
    db=Database(str(tmp_path/'db.sqlite')); db.migrate()
    scopes={'https://api.ebay.com/oauth/api_scope/sell.account','https://api.ebay.com/oauth/api_scope/sell.inventory','https://api.ebay.com/oauth/api_scope/sell.fulfillment'}
    activation=ProductionSellerValidator(cfg(),db,Client()).validate('seller',scopes)
    assert activation['seller_id']=='seller'
    require_production_activation(cfg(),db,'seller')
    with pytest.raises(EbayError): require_production_activation(cfg(payment_policy_id='changed'),db,'seller')
    with pytest.raises(EbayError): ProductionSellerValidator(cfg(),db,Client()).validate('other',scopes)

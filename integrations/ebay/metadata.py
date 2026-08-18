from datetime import datetime, timedelta, timezone
import json
from .errors import EbayError
from db import now

class EbayMetadataService:
    def __init__(self,config,db,client): self.cfg=config; self.db=db; self.client=client
    def _cached(self,key,loader,refresh=False):
        with self.db.connect() as c: row=c.execute("SELECT * FROM ebay_metadata_cache WHERE cache_key=?",(key,)).fetchone()
        if row and not refresh and row["expires_at"]>now(): return json.loads(row["payload"])
        try: value=loader()
        except EbayError:
            if row: return json.loads(row["payload"])
            raise
        expiry=(datetime.now(timezone.utc)+timedelta(seconds=self.cfg.metadata_cache_seconds)).isoformat()
        with self.db.transaction() as c: c.execute("INSERT OR REPLACE INTO ebay_metadata_cache VALUES(?,?,?,?)",(key,json.dumps(value),expiry,now()))
        return value
    def tree(self,refresh=False):
        key="tree:"+self.cfg.marketplace_id
        return self._cached(key,lambda:self.client.request("GET",f"/commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id={self.cfg.marketplace_id}"),refresh)
    def suggestions(self,query):
        tree=self.tree()["categoryTreeId"]
        from urllib.parse import quote
        return self.client.request("GET",f"/commerce/taxonomy/v1/category_tree/{tree}/get_category_suggestions?q={quote(query)}")
    def aspects(self,category_id,refresh=False):
        tree=self.tree()["categoryTreeId"]; key=f"aspects:{tree}:{category_id}"
        return self._cached(key,lambda:self.client.request("GET",f"/commerce/taxonomy/v1/category_tree/{tree}/get_item_aspects_for_category?category_id={category_id}"),refresh)
    def conditions(self,category_id,refresh=False):
        key=f"conditions:{self.cfg.marketplace_id}:{category_id}"
        return self._cached(key,lambda:self.client.request("GET",f"/sell/metadata/v1/marketplace/{self.cfg.marketplace_id}/get_item_condition_policies?filter=categoryIds:%7B{category_id}%7D"),refresh)

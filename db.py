import json, os, sqlite3, secrets
from contextlib import contextmanager
from datetime import datetime, timezone

STATUSES = ("Draft", "Active", "Sold", "Delisted")
TRANSITIONS = {
    "Draft": {"Active", "Sold", "Delisted"}, "Active": {"Sold", "Delisted"},
    "Delisted": {"Active", "Sold"}, "Sold": set(),
}
def now(): return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path): self.path = path
    def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30); con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON"); con.execute("PRAGMA busy_timeout=30000"); return con
    def migrate(self):
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            root=os.path.join(os.path.dirname(__file__), "migrations")
            for name in sorted(n for n in os.listdir(root) if n.endswith(".sql")):
                if con.execute("SELECT 1 FROM schema_migrations WHERE version=?",(name,)).fetchone(): continue
                con.executescript(open(os.path.join(root,name), encoding="utf8").read())
                con.execute("INSERT INTO schema_migrations VALUES (?,?)",(name,now()))
    @contextmanager
    def transaction(self, immediate=False):
        con=self.connect()
        try:
            if immediate: con.execute("BEGIN IMMEDIATE")
            yield con; con.commit()
        except Exception: con.rollback(); raise
        finally: con.close()
    def create_item(self, photos=None, **v):
        """Atomically allocate the next human-readable SKU and insert photo metadata."""
        stamp=now(); fields=("title","description","price","currency","quantity","condition","brand","size","color","category","photo_urls")
        vals=[json.dumps(v.get(k, [])) if k=="photo_urls" else v.get(k) for k in fields]
        with self.transaction(immediate=True) as c:
            # Explicit SKUs remain accepted for the isolated legacy Sandbox test
            # harness; the seller workflow never accepts or sends one.
            sku=v.get("sku")
            if not sku:
                highest=c.execute("SELECT COALESCE(MAX(CAST(SUBSTR(sku,4) AS INTEGER)),0) FROM items WHERE sku GLOB 'VO-[0-9]*'").fetchone()[0]
                sku=f"VO-{highest+1:06d}"
            cur=c.execute(f"INSERT INTO items (sku,{','.join(fields)},created_at,updated_at) VALUES (?,{','.join('?' for _ in fields)},?,?)", [sku]+vals+[stamp,stamp])
            for order,p in enumerate(photos or []):
                c.execute("INSERT INTO item_photos(item_id,filename,original_filename,display_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",(cur.lastrowid,p["filename"],p["original_filename"],order,stamp,stamp))
            return self.get_item(cur.lastrowid,c)
    def get_item(self, item_id, con=None):
        own=con is None; con=con or self.connect(); row=con.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
        result=dict(row) if row else None
        if result:
            result["photos"]=[dict(x) for x in con.execute("SELECT * FROM item_photos WHERE item_id=? ORDER BY display_order,id",(item_id,))]
            result["item_specifics"]=json.loads(result.get("item_specifics") or "{}")
        if own: con.close()
        return result
    @staticmethod
    def _public_item(row, con):
        """Return the deliberately small, ID-free representation used by the shop."""
        item={key: row[key] for key in ("sku","title","description","price","currency","quantity","condition","brand","size","color","category","status","created_at")}
        item["photos"]=[{"filename": p["filename"], "original_filename": p["original_filename"]} for p in con.execute("SELECT filename,original_filename FROM item_photos WHERE item_id=? ORDER BY display_order,id",(row["id"],))]
        return item
    def get_storefront_item_by_sku(self, sku):
        """Look up a visible product by its public SKU without returning internal IDs."""
        with self.connect() as con:
            row=con.execute("SELECT * FROM items WHERE sku=? AND storefront_visible=1",(sku,)).fetchone()
            return self._public_item(row,con) if row else None
    def storefront_catalog(self, search=None, brand=None, size=None, min_price=None, max_price=None, sort="newest"):
        """Query visible, active inventory and return only storefront-safe fields."""
        clauses=["storefront_visible=1","status='Active'"]; args=[]
        if search:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ? OR LOWER(COALESCE(brand,'')) LIKE ?)")
            term=f"%{search.lower()}%"; args.extend((term,term,term))
        if brand: clauses.append("LOWER(COALESCE(brand,''))=LOWER(?)"); args.append(brand)
        if size: clauses.append("LOWER(COALESCE(size,''))=LOWER(?)"); args.append(size)
        if min_price is not None: clauses.append("CAST(price AS NUMERIC)>=?"); args.append(str(min_price))
        if max_price is not None: clauses.append("CAST(price AS NUMERIC)<=?"); args.append(str(max_price))
        orders={"newest":"created_at DESC,id DESC","price_asc":"CAST(price AS NUMERIC) ASC,id DESC","price_desc":"CAST(price AS NUMERIC) DESC,id DESC"}
        if sort not in orders: raise ValueError("Invalid storefront sort")
        with self.connect() as con:
            rows=con.execute("SELECT * FROM items WHERE "+" AND ".join(clauses)+" ORDER BY "+orders[sort],args).fetchall()
            return [self._public_item(row,con) for row in rows]
    def inventory(self, status=None):
        with self.connect() as c:
            sql="SELECT * FROM items"; args=[]
            if status in STATUSES: sql+=" WHERE status=?"; args=[status]
            rows=c.execute(sql+" ORDER BY created_at DESC,id DESC",args).fetchall()
            return [self.get_item(r["id"],c) for r in rows]
    def get_photos(self,item_id): return (self.get_item(item_id) or {}).get("photos",[])
    def update_item(self,item_id,**values):
        allowed={"title","description","price","currency","quantity","condition","brand","size","color","category","photo_urls","item_specifics","ebay_sku","ebay_offer_id","ebay_listing_id","ebay_publication_state","ebay_sync_state","local_revision","synced_revision","publication_lock_token","publication_lock_expires_at","last_error","status","storefront_visible"}
        if not values: return self.get_item(item_id)
        if set(values)-allowed: raise ValueError("Unsupported item field")
        for key in ("photo_urls","item_specifics"):
            if key in values and not isinstance(values[key],str): values[key]=json.dumps(values[key])
        values["updated_at"]=now()
        with self.transaction() as c: c.execute("UPDATE items SET "+",".join(f"{k}=?" for k in values)+" WHERE id=?",[*values.values(),item_id])
        return self.get_item(item_id)
    def transition_status(self,item_id,status,con=None):
        if status not in STATUSES: raise ValueError("Invalid item status")
        item=self.get_item(item_id,con)
        if not item: raise ValueError("Item not found")
        if status != item["status"] and status not in TRANSITIONS[item["status"]]: raise ValueError(f"Cannot transition {item['status']} to {status}")
        if con:
            con.execute("UPDATE items SET status=?,updated_at=? WHERE id=?",(status,now(),item_id)); return self.get_item(item_id,con)
        return self.update_item(item_id,status=status)
    def update_marketplace(self,item_id,**values): return self.update_item(item_id,**values)
    def update_photo_publication(self,photo_id,url,fingerprint,error):
        with self.transaction() as c: c.execute("UPDATE item_photos SET remote_url=?,source_fingerprint=?,publication_error=?,updated_at=? WHERE id=?",(url,fingerprint,error,now(),photo_id))
    def update_item_and_photos(self,item_id,values,photos=None):
        """Atomically accept canonical edits and optional replacement photo rows."""
        allowed={"title","description","price","quantity","condition","category","brand","size","color","item_specifics"}
        if set(values)-allowed: raise ValueError("Unsupported editable item field")
        stamp=now(); values=dict(values); values["item_specifics"]=json.dumps(values.get("item_specifics",{}))
        with self.transaction(immediate=True) as c:
            c.execute("UPDATE items SET "+",".join(f"{k}=?" for k in values)+",local_revision=local_revision+1,ebay_sync_state=?,updated_at=? WHERE id=?",[*values.values(),"pending",stamp,item_id])
            if photos is not None:
                c.execute("DELETE FROM item_photos WHERE item_id=?",(item_id,))
                for order,p in enumerate(photos): c.execute("INSERT INTO item_photos(item_id,filename,original_filename,display_order,created_at,updated_at) VALUES(?,?,?,?,?,?)",(item_id,p["filename"],p["original_filename"],order,stamp,stamp))
            return self.get_item(item_id,c)
    def acquire_publication_lock(self,item_id,seconds=120):
        from datetime import timedelta
        token=secrets.token_urlsafe(24); expiry=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
        with self.transaction(immediate=True) as c:
            row=c.execute("SELECT publication_lock_token,publication_lock_expires_at FROM items WHERE id=?",(item_id,)).fetchone()
            if row["publication_lock_token"] and row["publication_lock_expires_at"] and row["publication_lock_expires_at"]>now(): return None
            c.execute("UPDATE items SET publication_lock_token=?,publication_lock_expires_at=? WHERE id=?",(token,expiry,item_id))
        return token
    def release_publication_lock(self,item_id,token):
        with self.transaction() as c: c.execute("UPDATE items SET publication_lock_token=NULL,publication_lock_expires_at=NULL WHERE id=? AND publication_lock_token=?",(item_id,token))
    def process_order_line(self,order_id,line_id,sku,listing_id,quantity,payment,fulfillment,raw):
        """Deduplicate an order line and atomically reduce immediately-visible stock."""
        with self.transaction(immediate=True) as c:
            if c.execute("SELECT 1 FROM ebay_order_lines WHERE order_id=? AND line_item_id=?",(order_id,line_id)).fetchone(): return False
            c.execute("INSERT INTO ebay_order_lines(order_id,line_item_id,sku,listing_id,quantity,payment_state,fulfillment_state,raw_payload,processed_at) VALUES(?,?,?,?,?,?,?,?,?)",(order_id,line_id,sku,listing_id,quantity,payment,fulfillment,json.dumps(raw),now()))
            if payment not in ("PAID","FULLY_REFUNDED"):
                return True
            row=c.execute("SELECT * FROM items WHERE ebay_sku=? OR ebay_listing_id=?",(sku,listing_id)).fetchone()
            if row and payment=="PAID":
                remaining=max(0,row["quantity"]-quantity); status="Sold" if remaining==0 else row["status"]
                c.execute("UPDATE items SET quantity=?,status=?,storefront_visible=?,ebay_sync_state='sold',updated_at=? WHERE id=?",(remaining,status,0 if remaining==0 else row["storefront_visible"],now(),row["id"]))
            return True

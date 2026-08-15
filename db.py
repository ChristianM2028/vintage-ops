import json, os, sqlite3
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
        if result: result["photos"]=[dict(x) for x in con.execute("SELECT * FROM item_photos WHERE item_id=? ORDER BY display_order,id",(item_id,))]
        if own: con.close()
        return result
    def inventory(self, status=None):
        with self.connect() as c:
            sql="SELECT * FROM items"; args=[]
            if status in STATUSES: sql+=" WHERE status=?"; args=[status]
            rows=c.execute(sql+" ORDER BY created_at DESC,id DESC",args).fetchall()
            return [self.get_item(r["id"],c) for r in rows]
    def get_photos(self,item_id): return (self.get_item(item_id) or {}).get("photos",[])
    def update_item(self,item_id,**values):
        allowed={"title","description","price","currency","quantity","condition","brand","size","color","category","photo_urls","ebay_sku","ebay_offer_id","ebay_listing_id","ebay_publication_state","last_error","status"}
        if not values: return self.get_item(item_id)
        if set(values)-allowed: raise ValueError("Unsupported item field")
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

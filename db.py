import json, os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

def now(): return datetime.now(timezone.utc).isoformat()

class Database:
    def __init__(self, path): self.path = path
    def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        con = sqlite3.connect(self.path); con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON"); return con
    def migrate(self):
        with self.connect() as con, open(os.path.join(os.path.dirname(__file__), "migrations/001_initial.sql")) as f:
            con.executescript(f.read())
    @contextmanager
    def transaction(self):
        con=self.connect()
        try: yield con; con.commit()
        except Exception: con.rollback(); raise
        finally: con.close()
    def create_item(self, **v):
        stamp=now(); fields=("sku","title","description","price","currency","quantity","condition","brand","size","color","category","photo_urls")
        vals=[json.dumps(v.get(k, [])) if k=="photo_urls" else v.get(k) for k in fields]
        with self.transaction() as c:
            cur=c.execute(f"INSERT INTO items ({','.join(fields)},created_at,updated_at) VALUES ({','.join('?' for _ in fields)},?,?)", vals+[stamp,stamp]); return self.get_item(cur.lastrowid, c)
    def get_item(self, item_id, con=None):
        own=con is None; con=con or self.connect(); row=con.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
        if own: con.close()
        return dict(row) if row else None
    def update_item(self,item_id,**values):
        if not values: return self.get_item(item_id)
        values["updated_at"]=now()
        with self.transaction() as c:
            c.execute("UPDATE items SET "+",".join(f"{k}=?" for k in values)+" WHERE id=?",[*values.values(),item_id])
        return self.get_item(item_id)

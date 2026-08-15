from contextlib import asynccontextmanager
import json
from cryptography.fernet import Fernet
from db import now

class EncryptedStorageRepository:
    def __init__(self,db,encryption_key,account_key="default"):
        if not encryption_key: raise ValueError("TOKEN_ENCRYPTION_KEY is required for browser sessions")
        self.db=db; self.fernet=Fernet(encryption_key.encode() if isinstance(encryption_key,str) else encryption_key); self.account_key=account_key
    async def load_encrypted(self,marketplace):
        with self.db.connect() as c: row=c.execute("SELECT encrypted_storage_state,session_expires_at,revoked_at FROM marketplace_accounts WHERE marketplace=? AND account_key=?",(marketplace,self.account_key)).fetchone()
        if not row or not row["encrypted_storage_state"] or row["revoked_at"]: return None
        return json.loads(self.fernet.decrypt(row["encrypted_storage_state"]).decode())
    async def save_encrypted(self,marketplace,state,expires_at=None):
        encrypted=self.fernet.encrypt(json.dumps(state).encode()); stamp=now()
        with self.db.transaction() as c: c.execute("INSERT INTO marketplace_accounts(marketplace,account_key,encrypted_storage_state,session_expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(marketplace,account_key) DO UPDATE SET encrypted_storage_state=excluded.encrypted_storage_state,session_expires_at=excluded.session_expires_at,revoked_at=NULL,updated_at=excluded.updated_at",(marketplace,self.account_key,encrypted,expires_at,stamp,stamp))
    async def revoke(self,marketplace):
        with self.db.transaction() as c: c.execute("UPDATE marketplace_accounts SET encrypted_storage_state=NULL,revoked_at=?,updated_at=? WHERE marketplace=? AND account_key=?",(now(),now(),marketplace,self.account_key))

class BrowserContextOwner:
    def __init__(self,browser_type,storage_repository,trace_failures=True): self.browser_type=browser_type; self.storage_repository=storage_repository; self.trace_failures=trace_failures
    @asynccontextmanager
    async def open(self,adapter,run):
        state=await self.storage_repository.load_encrypted(adapter.name)
        browser=await self.browser_type.launch(); context=await browser.new_context(storage_state=state)
        page=await context.new_page()
        try: yield page
        finally:
            state=await context.storage_state(); await self.storage_repository.save_encrypted(adapter.name,state)
            await context.close(); await browser.close()

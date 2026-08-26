import base64, hashlib, secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from .errors import EbayError, parse_error
from db import now

SCOPES=("https://api.ebay.com/oauth/api_scope "
        "https://api.ebay.com/oauth/api_scope/sell.inventory "
        "https://api.ebay.com/oauth/api_scope/sell.account "
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment")

class OAuthManager:
    def __init__(self, config, db, session):
        self.cfg=config; self.db=db; self.http=session
        keys=([config.encryption_key]+list(config.historical_encryption_keys)) if config.encryption_key else []
        self.fernet=MultiFernet([Fernet(key.encode()) for key in keys]) if keys else None
    @property
    def identity_base(self): return "https://auth.sandbox.ebay.com" if self.cfg.environment=="sandbox" else "https://auth.ebay.com"
    @property
    def api_base(self): return "https://api.sandbox.ebay.com" if self.cfg.environment=="sandbox" else "https://api.ebay.com"
    def _require_crypto(self):
        if not self.fernet: raise EbayError("configuration","TOKEN_ENCRYPTION_KEY is required; generate a Fernet key")
    def begin(self):
        missing=[name for name,value in (("EBAY_CLIENT_ID",self.cfg.client_id),("EBAY_CLIENT_SECRET",self.cfg.client_secret),("EBAY_RUNAME",self.cfg.runame),("TOKEN_ENCRYPTION_KEY",self.cfg.encryption_key)) if not value]
        if missing: raise EbayError("configuration","Configure "+", ".join(missing)+" before connecting the seller")
        state=secrets.token_urlsafe(32); digest=hashlib.sha256(state.encode()).hexdigest()
        expiry=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
        with self.db.transaction() as c: c.execute("INSERT INTO oauth_states(state_hash,expires_at) VALUES (?,?)",(digest,expiry))
        return self.identity_base+"/oauth2/authorize?"+urlencode({"client_id":self.cfg.client_id,"redirect_uri":self.cfg.runame,"response_type":"code","scope":SCOPES,"state":state}),state
    def validate_state(self,state):
        digest=hashlib.sha256((state or "").encode()).hexdigest(); stamp=now()
        with self.db.transaction() as c:
            row=c.execute("SELECT * FROM oauth_states WHERE state_hash=?",(digest,)).fetchone()
            if not row or row["used_at"] or row["expires_at"] < stamp: raise EbayError("authentication","OAuth state is invalid, expired, or already used; start authorization again")
            c.execute("UPDATE oauth_states SET used_at=? WHERE state_hash=?",(stamp,digest))
    def _auth(self): return "Basic "+base64.b64encode(f"{self.cfg.client_id}:{self.cfg.client_secret}".encode()).decode()
    def exchange_code(self,code):
        self._require_crypto()
        r=self.http.post(self.api_base+"/identity/v1/oauth2/token",headers={"Authorization":self._auth(),"Content-Type":"application/x-www-form-urlencoded"},data={"grant_type":"authorization_code","code":code,"redirect_uri":self.cfg.runame},timeout=self.cfg.timeout)
        if not r.ok: raise parse_error(r)
        self._save(r.json()); return True
    def _save(self,data,old_refresh=None):
        refresh=data.get("refresh_token") or old_refresh
        if not refresh: raise EbayError("authentication","eBay did not return a refresh token; reauthorize the seller")
        stamp=datetime.now(timezone.utc); access_exp=(stamp+timedelta(seconds=int(data.get("expires_in",0))-30)).isoformat()
        refresh_exp=(stamp+timedelta(seconds=int(data.get("refresh_token_expires_in",47304000)))).isoformat()
        enc=lambda value:self.fernet.encrypt(value.encode()) if value else None
        with self.db.transaction() as c: c.execute("INSERT OR REPLACE INTO ebay_tokens VALUES (1,?,?,?,?,?,?)",(enc(refresh),enc(data.get("access_token")),access_exp,refresh_exp,data.get("scope",SCOPES),now()))
    def access_token(self):
        self._require_crypto()
        with self.db.connect() as c: row=c.execute("SELECT * FROM ebay_tokens WHERE id=1").fetchone()
        if not row: raise EbayError("authentication","Seller is not authorized; connect the eBay seller")
        granted=set((row["scopes"] or "").split()); required=set(SCOPES.split())
        if not required.issubset(granted): raise EbayError("reauthorization_required","Seller authorization is missing required Inventory, Account, or Fulfillment permissions; reconnect the seller")
        dec=lambda value:self.fernet.decrypt(value).decode() if value else None
        try: access=dec(row["access_token_encrypted"]); refresh=dec(row["refresh_token_encrypted"])
        except InvalidToken: raise EbayError("configuration","Stored seller token cannot be decrypted with TOKEN_ENCRYPTION_KEY")
        if access and row["access_expires_at"] > now(): return access
        r=self.http.post(self.api_base+"/identity/v1/oauth2/token",headers={"Authorization":self._auth(),"Content-Type":"application/x-www-form-urlencoded"},data={"grant_type":"refresh_token","refresh_token":refresh,"scope":row["scopes"]},timeout=self.cfg.timeout)
        if not r.ok:
            error=parse_error(r)
            if r.status_code in (400,401): raise EbayError("reauthorization_required","Seller authorization was revoked or expired; connect the seller again",status=r.status_code,details=error.details,request_id=error.request_id,rlogid=error.rlogid)
            raise error
        self._save(r.json(),refresh); return r.json()["access_token"]

import requests
from .errors import EbayError, parse_error

class EbayClient:
    def __init__(self, config, oauth, session=None): self.cfg=config; self.oauth=oauth; self.http=session or requests.Session()
    @property
    def base(self): return "https://api.sandbox.ebay.com" if self.cfg.environment=="sandbox" else "https://api.ebay.com"
    def request(self,method,path,*,json=None,expected=(200,201,204)):
        try:
            r=self.http.request(method,self.base+path,headers={"Authorization":"Bearer "+self.oauth.access_token(),"Content-Type":"application/json","Content-Language":"en-US","X-EBAY-C-MARKETPLACE-ID":self.cfg.marketplace_id},json=json,timeout=self.cfg.timeout)
        except requests.Timeout: raise EbayError("remote","eBay request timed out; retry safely")
        except requests.RequestException: raise EbayError("remote","Could not reach eBay; retry when connectivity is restored")
        if r.status_code not in expected: raise parse_error(r)
        if not r.content: return {}
        try: return r.json()
        except ValueError: return {}

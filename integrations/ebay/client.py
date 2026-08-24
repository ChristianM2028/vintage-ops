import logging
from urllib.parse import urlsplit

import requests

from .errors import EbayError, _header, parse_error


logger = logging.getLogger(__name__)

class EbayClient:
    def __init__(self, config, oauth, session=None): self.cfg=config; self.oauth=oauth; self.http=session or requests.Session()
    @property
    def base(self): return "https://api.sandbox.ebay.com" if self.cfg.environment=="sandbox" else "https://api.ebay.com"
    def request(self,method,path,*,json=None,files=None,data=None,expected=(200,201,204)):
        normalized_method=method.upper()
        headers={"Authorization":"Bearer "+self.oauth.access_token(),"Content-Language":"en-US","X-EBAY-C-MARKETPLACE-ID":self.cfg.marketplace_id}
        # requests must generate the multipart boundary.  Supplying a JSON
        # content type here makes the Media API reject an otherwise valid body.
        if files is None: headers["Content-Type"]="application/json"
        try:
            r=self.http.request(normalized_method,self.base+path,headers=headers,json=json,files=files,data=data,timeout=self.cfg.timeout)
        except requests.Timeout: raise EbayError("remote","eBay request timed out; retry safely")
        except requests.RequestException: raise EbayError("remote","Could not reach eBay; retry when connectivity is restored")
        parsed=urlsplit(path)
        relative_path=parsed.path+("?"+parsed.query if parsed.query else "")
        try: errors=r.json().get("errors",[])
        except (ValueError,AttributeError): errors=[]
        fields={"method":normalized_method,"path":relative_path,"status":r.status_code}
        if errors and errors[0].get("errorId") is not None: fields["error_id"]=errors[0]["errorId"]
        for header,field in (("rlogid","rlogid"),("x-ebay-c-request-id","x-ebay-c-request-id"),("x-ebay-correlation-id","x-ebay-correlation-id")):
            value=_header(r.headers,header)
            if value is not None: fields[field]=value
        # Keep the event deliberately limited to operational correlation data.
        logger.info("eBay response",extra={"ebay_response":fields})
        if r.status_code not in expected:
            error=parse_error(r)
            error.method=normalized_method
            error.path=parsed.path
            raise error
        if not getattr(r,"content",None): return {}
        try: return r.json()
        except ValueError: return {}

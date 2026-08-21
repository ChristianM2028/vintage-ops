class EbayError(Exception):
    def __init__(self, kind, message, *, status=None, details=None, request_id=None, rlogid=None):
        super().__init__(message); self.kind=kind; self.status=status; self.details=details or []; self.request_id=request_id; self.rlogid=rlogid
    def safe_dict(self):
        return {"kind":self.kind,"message":str(self),"status":self.status,"request_id":self.request_id,"rlogid":self.rlogid,"details":self.details}

def _header(headers, name):
    """Read a response header without relying on a case-aware mapping."""
    wanted=name.lower()
    return next((value for key,value in headers.items() if key.lower()==wanted),None)

def parse_error(response):
    try: body=response.json()
    except ValueError: body={}
    details=[]
    for e in body.get("errors",[]):
        details.append({k:e.get(k) for k in ("errorId","domain","category","message","longMessage","parameters") if e.get(k) is not None})
    status=response.status_code
    kind="authentication" if status in (401,403) else "conflict" if status==409 else "invalid_item" if status==400 else "remote"
    message=(details[0].get("message") if details else None) or f"eBay request failed with HTTP {status}"
    return EbayError(kind,message,status=status,details=details,
                     request_id=_header(response.headers,"x-ebay-c-request-id") or _header(response.headers,"x-ebay-correlation-id"),
                     rlogid=_header(response.headers,"rlogid"))

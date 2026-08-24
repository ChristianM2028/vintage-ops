class EbayError(Exception):
    def __init__(self, kind, message, *, status=None, details=None, request_id=None, rlogid=None, method=None, path=None, missing_aspects=None):
        super().__init__(message); self.kind=kind; self.status=status; self.details=details or []; self.request_id=request_id; self.rlogid=rlogid; self.method=method; self.path=path; self.missing_aspects=missing_aspects or []
    def safe_dict(self):
        result={"kind":self.kind,"message":str(self),"status":self.status,"request_id":self.request_id,"rlogid":self.rlogid,"method":self.method,"path":self.path,"details":self.details}
        if self.missing_aspects: result["missing_aspects"]=self.missing_aspects
        return result

def missing_aspects(details):
    """Extract structured Inventory API missing-aspect parameters."""
    found=[]
    for error in details:
        for parameter in error.get("parameters") or []:
            name=(parameter.get("name") or "").casefold()
            if name in {"aspectname","aspect","missingaspect","missingaspects"}:
                values=parameter.get("value") or parameter.get("values") or []
                if isinstance(values,str): values=[x.strip() for x in values.split(",")]
                found.extend(x for x in values if x and x not in found)
    return found

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
    return EbayError(kind,message,status=status,details=details,missing_aspects=missing_aspects(details),
                     request_id=_header(response.headers,"x-ebay-c-request-id") or _header(response.headers,"x-ebay-correlation-id"),
                     rlogid=_header(response.headers,"rlogid"))

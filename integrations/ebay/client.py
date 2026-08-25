import logging
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests

from .errors import EbayError, _header, parse_error


logger = logging.getLogger(__name__)

_TRADING_PATH = "/ws/api.dll"
_ERROR_FIELDS = {"ErrorCode", "SeverityCode", "ShortMessage", "LongMessage"}


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _safe_text(value):
    """Bound diagnostic text and remove credential/file-like values."""
    value = " ".join((value or "").split())[:500]
    if re.search(r"(?i)(bearer|oauth|authorization|access[_ -]?token)", value):
        return "[redacted]"
    if re.search(r"(?i)(?:[/\\]|\.(?:jpe?g|png|gif|webp)\b)", value):
        return "[redacted]"
    return value


def parse_upload_site_hosted_pictures(xml_content):
    """Return a safe, protocol-neutral result for the Trading EPS call."""
    try:
        root = ElementTree.fromstring(xml_content)
    except (ElementTree.ParseError, TypeError, ValueError):
        raise EbayError("remote", "eBay returned an invalid image upload response")

    def descendants(node, name):
        return [child for child in node.iter() if _local_name(child.tag) == name]

    ack_nodes = descendants(root, "Ack")
    ack = (ack_nodes[0].text or "").strip() if ack_nodes else ""
    errors = []
    for error_node in descendants(root, "Errors"):
        safe = {}
        for child in error_node:
            name = _local_name(child.tag)
            if name in _ERROR_FIELDS and child.text:
                safe[name] = _safe_text(child.text)
            elif name == "ErrorParameters":
                param_id = _safe_text(child.attrib.get("ParamID", ""))
                values = descendants(child, "Value")
                value = _safe_text(values[0].text) if values and values[0].text else ""
                if param_id and value:
                    safe.setdefault("ErrorParameters", []).append({"ParamID": param_id, "Value": value})
        if safe:
            errors.append(safe)
    urls = descendants(root, "FullURL")
    url = (urls[0].text or "").strip() if urls else None
    if not (isinstance(url, str) and url.startswith("https://")):
        url = None
    result = {"ack": ack, "errors": errors, "url": url, "provider_id": None, "provider": "trading"}
    normalized_ack = ack.casefold()
    if normalized_ack in ("failure", "partialfailure"):
        diagnostic = errors[0] if errors else {}
        message = diagnostic.get("ShortMessage") or diagnostic.get("LongMessage") or "eBay image upload failed"
        raise EbayError("remote", message, details=errors)
    if normalized_ack not in ("success", "warning"):
        raise EbayError("remote", "eBay returned an invalid image upload acknowledgement", details=errors)
    return result

class EbayClient:
    def __init__(self, config, oauth, session=None): self.cfg=config; self.oauth=oauth; self.http=session or requests.Session()
    @property
    def base(self): return "https://api.sandbox.ebay.com" if self.cfg.environment=="sandbox" else "https://api.ebay.com"
    def request(self,method,path,*,json=None,files=None,data=None,expected=(200,201,204),request_headers=None):
        normalized_method=method.upper()
        headers={"Authorization":"Bearer "+self.oauth.access_token(),"Content-Language":"en-US","X-EBAY-C-MARKETPLACE-ID":self.cfg.marketplace_id}
        # requests must generate the multipart boundary.  Supplying a JSON
        # content type here makes the Media API reject an otherwise valid body.
        if files is None: headers["Content-Type"]="application/json"
        if request_headers: headers.update(request_headers)
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
        if (parsed.path == _TRADING_PATH and request_headers and
                request_headers.get("X-EBAY-API-CALL-NAME") == "UploadSiteHostedPictures"):
            return parse_upload_site_hosted_pictures(r.content)
        try: return r.json()
        except ValueError: return {}

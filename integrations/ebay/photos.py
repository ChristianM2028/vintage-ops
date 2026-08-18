import hashlib
from pathlib import Path
from urllib.parse import quote
import requests
from .errors import EbayError

class PhotoPublisher:
    """Publishes immutable, content-addressed photos to a configured HTTPS media service."""
    def __init__(self, config, db, session=None):
        self.cfg=config; self.db=db; self.http=session or requests.Session()
    def publish(self,item):
        if not self.cfg.media_public_base_url.startswith("https://") or not self.cfg.media_upload_url.startswith("https://"):
            raise EbayError("photo_configuration","Configure HTTPS MEDIA_PUBLIC_BASE_URL and MEDIA_UPLOAD_URL")
        results=[]; failures=[]
        for photo in item.get("photos",[]):
            path=Path(self.cfg.upload_directory)/photo["filename"]
            try:
                data=path.read_bytes(); digest=hashlib.sha256(data).hexdigest()
                if photo.get("remote_url") and photo.get("source_fingerprint")==digest: url=photo["remote_url"]
                else:
                    key=f"{digest}{path.suffix.lower()}"; target=self.cfg.media_upload_url.rstrip("/")+"/"+quote(key)
                    headers={"Content-Type":"application/octet-stream"}
                    if self.cfg.media_upload_token: headers["Authorization"]="Bearer "+self.cfg.media_upload_token
                    response=self.http.put(target,data=data,headers=headers,timeout=self.cfg.timeout)
                    if response.status_code not in (200,201,204): raise RuntimeError(f"media host returned HTTP {response.status_code}")
                    url=self.cfg.media_public_base_url.rstrip("/")+"/"+quote(key)
                    self.db.update_photo_publication(photo["id"],url,digest,None)
                results.append(url)
            except Exception as exc:
                message=str(exc); failures.append({"photo_id":photo["id"],"filename":photo["original_filename"],"message":message})
                self.db.update_photo_publication(photo["id"],photo.get("remote_url"),photo.get("source_fingerprint"),message)
        if failures: raise EbayError("photo_partial_failure","One or more photos could not be published",details=failures)
        self.db.update_item(item["id"],photo_urls=results)
        return results

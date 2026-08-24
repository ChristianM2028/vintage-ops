import hashlib
from pathlib import Path

from .errors import EbayError


class EbayImagePublisher:
    """Synchronize local item photos to eBay Picture Services (EPS)."""

    PATH = "/commerce/media/v1_beta/image/create_image_from_file"

    def __init__(self, config, db, client):
        self.cfg = config
        self.db = db
        self.client = client

    def publish(self, item):
        if item.get("ebay_integration_mode") not in ("ebay_sandbox", "ebay_production"):
            raise EbayError("photo_configuration", "Local mock items cannot upload photos to eBay")
        results = []
        failures = []
        for photo in item.get("photos", []):
            path = Path(self.cfg.upload_directory) / photo["filename"]
            try:
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                image_id = photo.get("ebay_image_id")
                url = photo.get("ebay_eps_url")
                if photo.get("ebay_source_fingerprint") != digest or not image_id or not url:
                    response = self.client.request(
                        "POST", self.PATH,
                        files={"image": (photo["original_filename"], data, "application/octet-stream")},
                        expected=(200, 201),
                    )
                    image_id = response.get("imageId") if isinstance(response, dict) else None
                    url = response.get("imageUrl") if isinstance(response, dict) else None
                    if not isinstance(image_id, str) or not image_id.strip() or not isinstance(url, str) or not url.startswith("https://"):
                        raise EbayError("remote", "eBay returned an incomplete image upload response")
                    self.db.update_photo_eps(photo["id"], image_id, url, digest, None)
                results.append(url)
            except EbayError as exc:
                message = str(exc)
                self.db.set_photo_eps_error(photo["id"], message)
                failures.append({"photo_id": photo["id"], "filename": photo["original_filename"], "message": message})
            except (OSError, ValueError) as exc:
                message = "The local image could not be prepared for eBay"
                self.db.set_photo_eps_error(photo["id"], message)
                failures.append({"photo_id": photo["id"], "filename": photo["original_filename"], "message": message})
        if failures:
            raise EbayError("photo_partial_failure", "One or more photos could not be uploaded to eBay", details=failures)
        self.db.promote_eps_photo_set(item["id"], results)
        return results


# Compatibility for extensions importing the former name.
PhotoPublisher = EbayImagePublisher

import hashlib
from pathlib import Path

from .errors import EbayError


class MediaImageTransport:
    PATH = "/commerce/media/v1_beta/image/create_image_from_file"

    def upload(self, client, filename, data):
        response = client.request("POST", self.PATH,
            files={"image": (filename, data, "application/octet-stream")}, expected=(200, 201))
        return {"url": response.get("imageUrl"), "provider_id": response.get("imageId"),
                "errors": response.get("errors", [])} if isinstance(response, dict) else {}


class TradingImageTransport:
    PATH = "/ws/api.dll"

    def upload(self, client, filename, data):
        # The XML request contains no local filename or credentials. requests
        # creates the multipart boundary, and the response parser discards XML.
        request_xml = b'<?xml version="1.0" encoding="utf-8"?><UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents"/>'
        return client.request("POST", self.PATH,
            files={"XML Payload": (None, request_xml, "text/xml"),
                   "dummy": ("image", data, "application/octet-stream")},
            request_headers={"X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
                             "X-EBAY-API-COMPATIBILITY-LEVEL": "1423"}, expected=(200,))


class EbayImagePublisher:
    """Synchronize local item photos to eBay Picture Services (EPS)."""

    def __init__(self, config, db, client, transport=None):
        self.cfg = config
        self.db = db
        self.client = client
        provider = getattr(config, "ebay_image_upload_api", "media")
        self.transport = transport or (TradingImageTransport() if provider == "trading" else MediaImageTransport())

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
                if photo.get("ebay_source_fingerprint") != digest or not (isinstance(url, str) and url.startswith("https://")):
                    response = self.transport.upload(self.client, photo["original_filename"], data)
                    image_id = response.get("provider_id")
                    url = response.get("url")
                    if not isinstance(url, str) or not url.startswith("https://"):
                        errors = response.get("errors") or []
                        diagnostic = errors[0] if errors else {}
                        message = diagnostic.get("ShortMessage") or diagnostic.get("LongMessage") or diagnostic.get("message")
                        raise EbayError("remote", message or "eBay returned an incomplete image upload response", details=errors)
                    self.db.update_photo_eps(photo["id"], image_id, url, digest, None)
                results.append(url)
            except EbayError as exc:
                message = str(exc)
                self.db.set_photo_eps_error(photo["id"], message)
                failures.append({"photo_id": photo["id"], "message": message})
            except (OSError, ValueError) as exc:
                message = "The local image could not be prepared for eBay"
                self.db.set_photo_eps_error(photo["id"], message)
                failures.append({"photo_id": photo["id"], "message": message})
        if failures:
            raise EbayError("photo_partial_failure", "One or more photos could not be uploaded to eBay", details=failures)
        self.db.promote_eps_photo_set(item["id"], results)
        return results


# Compatibility for extensions importing the former name.
PhotoPublisher = EbayImagePublisher

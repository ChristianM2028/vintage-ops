import base64, os, re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


DOTENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=DOTENV_PATH, override=False)


def env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}

@dataclass(frozen=True)
class Config:
    database_path: str = os.getenv("DATABASE_PATH", "instance/vintage_ops.sqlite3")
    secret_key: str = os.getenv("FLASK_SECRET_KEY", "development-only-change-me")
    encryption_key: str = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    client_id: str = os.getenv("EBAY_CLIENT_ID", "")
    client_secret: str = os.getenv("EBAY_CLIENT_SECRET", "")
    runame: str = os.getenv("EBAY_RUNAME", "")
    callback_url: str = os.getenv("EBAY_CALLBACK_URL", "http://127.0.0.1:5000/ebay/callback")
    marketplace_id: str = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")
    currency: str = os.getenv("EBAY_CURRENCY", "USD")
    location_key: str = os.getenv("EBAY_MERCHANT_LOCATION_KEY", "")
    fulfillment_policy_id: str = os.getenv("EBAY_FULFILLMENT_POLICY_ID", "")
    payment_policy_id: str = os.getenv("EBAY_PAYMENT_POLICY_ID", "")
    return_policy_id: str = os.getenv("EBAY_RETURN_POLICY_ID", "")
    category_id: str = os.getenv("EBAY_CATEGORY_ID", "")
    environment: str = os.getenv("EBAY_ENVIRONMENT", "sandbox")
    timeout: float = float(os.getenv("EBAY_TIMEOUT_SECONDS", "15"))
    marketplace_mock_mode: bool = env_bool("MARKETPLACE_MOCK_MODE", True)
    upload_directory: str = os.getenv("UPLOAD_DIRECTORY", "instance/uploads")
    backup_directory: str = os.getenv("BACKUP_DIRECTORY", "instance/backups")
    backup_encryption_key: str = os.getenv("BACKUP_ENCRYPTION_KEY", "")
    backup_s3_endpoint: str = os.getenv("BACKUP_S3_ENDPOINT", "")
    backup_s3_region: str = os.getenv("BACKUP_S3_REGION", "")
    backup_s3_bucket: str = os.getenv("BACKUP_S3_BUCKET", "")
    backup_s3_access_key_id: str = os.getenv("BACKUP_S3_ACCESS_KEY_ID", "")
    backup_s3_secret_access_key: str = os.getenv("BACKUP_S3_SECRET_ACCESS_KEY", "")
    backup_s3_prefix: str = os.getenv("BACKUP_S3_PREFIX", "vintage-ops/backups").strip(" /")
    backup_daily_retention: int = int(os.getenv("BACKUP_DAILY_RETENTION", "7"))
    backup_weekly_retention: int = int(os.getenv("BACKUP_WEEKLY_RETENTION", "4"))
    backup_scheduled_job_token: str = os.getenv("BACKUP_SCHEDULED_JOB_TOKEN", "")
    max_upload_size: int = int(os.getenv("MAX_UPLOAD_SIZE", str(8 * 1024 * 1024)))
    permitted_image_extensions: tuple = tuple(x.strip().lower().lstrip(".") for x in os.getenv("PERMITTED_IMAGE_EXTENSIONS", "jpg,jpeg,png,gif,webp").split(",") if x.strip())
    max_photo_count: int = int(os.getenv("MAX_PHOTO_COUNT", "10"))
    metadata_cache_seconds: int = int(os.getenv("EBAY_METADATA_CACHE_SECONDS", "86400"))
    # Explicit during the EPS-to-Media migration; never infer a protocol from a
    # response's content type.
    ebay_image_upload_api: str = os.getenv("EBAY_IMAGE_UPLOAD_API", "media").strip().lower()
    seller_username: str = os.getenv("SELLER_USERNAME", "")
    seller_password_hash: str = os.getenv("SELLER_PASSWORD_HASH", "")
    session_cookie_secure: bool = env_bool("SESSION_COOKIE_SECURE", False)
    proxy_fix_proto: int = int(os.getenv("PROXY_FIX_PROTO", "0"))
    proxy_fix_host: int = int(os.getenv("PROXY_FIX_HOST", "0"))
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "development-webhook-secret")
    stripe_success_url: str = os.getenv("STRIPE_SUCCESS_URL", "http://127.0.0.1:5000/shop/order/{ORDER_ID}/success")
    stripe_cancel_url: str = os.getenv("STRIPE_CANCEL_URL", "http://127.0.0.1:5000/shop/order/{ORDER_ID}/cancel")
    stripe_fake_mode: bool = env_bool("STRIPE_FAKE_MODE", True)
    reservation_seconds: int = int(os.getenv("STOREFRONT_RESERVATION_SECONDS", "1800"))
    scheduled_job_token: str = os.getenv("SCHEDULED_JOB_TOKEN", "")
    scheduled_job_lease_seconds: int = int(os.getenv("SCHEDULED_JOB_LEASE_SECONDS", "240"))
    scheduled_job_limit: int = int(os.getenv("SCHEDULED_JOB_LIMIT", "100"))
    ebay_order_page_size: int = int(os.getenv("EBAY_ORDER_PAGE_SIZE", "50"))
    ebay_order_max_pages: int = int(os.getenv("EBAY_ORDER_MAX_PAGES", "10"))
    ebay_order_max_orders: int = int(os.getenv("EBAY_ORDER_MAX_ORDERS", "500"))
    business_name: str = os.getenv("BUSINESS_NAME", "Vintage Ops")
    business_address: str = os.getenv("BUSINESS_ADDRESS", "")
    stripe_shipping_countries: tuple = tuple(
        country.strip().upper() for country in os.getenv("STRIPE_SHIPPING_COUNTRIES", "US").split(",")
        if country.strip()
    )

    def __post_init__(self):
        # This milestone is deliberately test-mode only.  Default deny even if
        # fake mode is enabled so a live credential cannot sit unnoticed.
        if self.stripe_secret_key.strip().startswith("sk_live_"):
            raise ValueError("Live Stripe secret keys are not supported")
        if not self.stripe_shipping_countries or any(
                not re.fullmatch(r"[A-Z]{2}", value) for value in self.stripe_shipping_countries):
            raise ValueError("Stripe shipping countries must be two-letter country codes")
        if self.proxy_fix_proto not in (0, 1) or self.proxy_fix_host not in (0, 1):
            raise ValueError("Proxy trust values must be 0 or 1")
        if self.ebay_image_upload_api not in ("media", "trading"):
            raise ValueError("EBAY_IMAGE_UPLOAD_API must be media or trading")
        if not 1 <= self.scheduled_job_limit <= 500 or not 30 <= self.scheduled_job_lease_seconds <= 3600:
            raise ValueError("Scheduled-job bounds are invalid")
        if not 1 <= self.ebay_order_page_size <= 200 or not 1 <= self.ebay_order_max_pages <= 100 or not 1 <= self.ebay_order_max_orders <= 5000:
            raise ValueError("eBay order-fetch bounds are invalid")
        if os.getenv("RAILWAY_ENVIRONMENT") and len(self.scheduled_job_token) < 32:
            raise ValueError("SCHEDULED_JOB_TOKEN must be a high-entropy value in hosted environments")
        if (not self.backup_s3_prefix or any(part in {"", ".", ".."} for part in self.backup_s3_prefix.split("/"))
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*",self.backup_s3_prefix)):
            raise ValueError("BACKUP_S3_PREFIX must be a non-empty normalized prefix")
        if not 1 <= self.backup_daily_retention <= 31 or not 1 <= self.backup_weekly_retention <= 52:
            raise ValueError("Backup retention bounds are invalid")
        hosted = bool(os.getenv("RAILWAY_ENVIRONMENT"))
        backup_values = (self.backup_encryption_key, self.backup_s3_endpoint,
                         self.backup_s3_region, self.backup_s3_bucket,
                         self.backup_s3_access_key_id, self.backup_s3_secret_access_key,
                         self.backup_scheduled_job_token)
        if hosted and any(not value for value in backup_values):
            raise ValueError("Hosted backup configuration is incomplete")
        endpoint=urlsplit(self.backup_s3_endpoint)
        if self.backup_s3_endpoint and (endpoint.scheme!="https" or not endpoint.netloc or endpoint.username or endpoint.password):
            raise ValueError("BACKUP_S3_ENDPOINT must use HTTPS")
        if self.backup_encryption_key:
            try:
                if not re.fullmatch(r"[A-Za-z0-9_-]{43}=",self.backup_encryption_key):
                    raise ValueError
                key = base64.urlsafe_b64decode(self.backup_encryption_key.encode("ascii"))
            except Exception as exc:
                raise ValueError("BACKUP_ENCRYPTION_KEY must be URL-safe base64") from exc
            if len(key) != 32:
                raise ValueError("BACKUP_ENCRYPTION_KEY must encode exactly 32 bytes")
        if hosted and len(self.backup_scheduled_job_token) < 32:
            raise ValueError("BACKUP_SCHEDULED_JOB_TOKEN must be a high-entropy value in hosted environments")

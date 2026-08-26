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
    app_environment: str = os.getenv("APP_ENVIRONMENT", "local").strip().lower()
    stripe_provider_mode: str = os.getenv("STRIPE_PROVIDER_MODE", "").strip().lower()
    ebay_provider_mode: str = os.getenv("EBAY_PROVIDER_MODE", "").strip().lower()
    canonical_origin: str = os.getenv("CANONICAL_ORIGIN", "").strip()
    production_provider_activation_id: str = os.getenv("PRODUCTION_PROVIDER_ACTIVATION_ID", "").strip()
    expected_stripe_account_id: str = os.getenv("EXPECTED_STRIPE_ACCOUNT_ID", "").strip()
    expected_ebay_seller_id: str = os.getenv("EXPECTED_EBAY_SELLER_ID", "").strip()
    database_path: str = os.getenv("DATABASE_PATH", "instance/vintage_ops.sqlite3")
    secret_key: str = os.getenv("FLASK_SECRET_KEY", "development-only-change-me")
    encryption_key: str = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    historical_encryption_keys: tuple = tuple(x.strip() for x in os.getenv("TOKEN_DECRYPT_ONLY_KEYS", "").split(",") if x.strip())
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
    session_idle_seconds: int = int(os.getenv("SESSION_IDLE_SECONDS", "1800"))
    session_absolute_seconds: int = int(os.getenv("SESSION_ABSOLUTE_SECONDS", "28800"))
    session_generation: str = os.getenv("SESSION_GENERATION", "local-generation")
    proxy_fix_proto: int = int(os.getenv("PROXY_FIX_PROTO", "0"))
    proxy_fix_host: int = int(os.getenv("PROXY_FIX_HOST", "0"))
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "development-webhook-secret")
    stripe_success_url: str = os.getenv("STRIPE_SUCCESS_URL", "http://127.0.0.1:5000/shop/order/{ORDER_ID}/success")
    stripe_cancel_url: str = os.getenv("STRIPE_CANCEL_URL", "http://127.0.0.1:5000/shop/order/{ORDER_ID}/cancel")
    stripe_fake_mode: bool = env_bool("STRIPE_FAKE_MODE", True)
    rate_limit_storage_uri: str = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
    login_rate_limit: str = os.getenv("LOGIN_RATE_LIMIT", "5 per minute")
    checkout_ip_rate_limit: str = os.getenv("CHECKOUT_IP_RATE_LIMIT", "20 per hour")
    checkout_sku_rate_limit: str = os.getenv("CHECKOUT_SKU_RATE_LIMIT", "5 per hour")
    order_status_rate_limit: str = os.getenv("ORDER_STATUS_RATE_LIMIT", "60 per minute")
    webhook_rate_limit: str = os.getenv("WEBHOOK_RATE_LIMIT", "600 per minute")
    internal_job_rate_limit: str = os.getenv("INTERNAL_JOB_RATE_LIMIT", "120 per minute")
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
        if self.app_environment not in {"local","sandbox","production"}:
            raise ValueError("APP_ENVIRONMENT must be local, sandbox, or production")
        stripe_mode=self.stripe_provider_mode or ("fake" if self.stripe_fake_mode else "test")
        ebay_mode=self.ebay_provider_mode or ("mock" if self.marketplace_mock_mode else self.environment)
        if stripe_mode not in {"fake","test","live"}: raise ValueError("Unknown Stripe provider mode")
        if ebay_mode not in {"mock","sandbox","production"}: raise ValueError("Unknown eBay provider mode")
        allowed={"local":({"fake","test"},{"mock","sandbox"}),"sandbox":({"fake","test"},{"mock","sandbox"}),"production":({"live"},{"production"})}
        if stripe_mode not in allowed[self.app_environment][0] or ebay_mode not in allowed[self.app_environment][1]:
            raise ValueError("Provider modes do not match APP_ENVIRONMENT")
        key=self.stripe_secret_key.strip()
        if stripe_mode=="live" and not key.startswith("sk_live_"): raise ValueError("Stripe live mode requires an sk_live_ key")
        if stripe_mode=="test" and (self.stripe_provider_mode or self.app_environment!="local") and not key.startswith("sk_test_"): raise ValueError("Stripe test mode requires an sk_test_ key")
        if self.stripe_provider_mode and stripe_mode=="fake" and key.startswith(("sk_live_","sk_test_")): raise ValueError("Stripe fake mode cannot contain a provider key")
        if stripe_mode!="live" and key.startswith("sk_live_"): raise ValueError("Live Stripe keys require validated production mode")
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
        if not 60 <= self.session_idle_seconds <= self.session_absolute_seconds <= 86400:
            raise ValueError("Session lifetime bounds are invalid")
        strict=self.app_environment in {"sandbox","production"} or hosted
        placeholders={"","development-only-change-me","development-webhook-secret","change-me","changeme","secret","password","local-generation"}
        def strong(name,value,minimum=32):
            if not value or len(value.strip())<minimum or value.strip().lower() in placeholders or "placeholder" in value.lower():
                raise ValueError(f"{name} must be a non-placeholder high-entropy value")
        if strict:
            for name,value in (("FLASK_SECRET_KEY",self.secret_key),("STRIPE_WEBHOOK_SECRET",self.stripe_webhook_secret),
                ("SELLER_USERNAME",self.seller_username),("SELLER_PASSWORD_HASH",self.seller_password_hash),
                ("SCHEDULED_JOB_TOKEN",self.scheduled_job_token),("BACKUP_SCHEDULED_JOB_TOKEN",self.backup_scheduled_job_token),
                ("TOKEN_ENCRYPTION_KEY",self.encryption_key),("BACKUP_ENCRYPTION_KEY",self.backup_encryption_key),("SESSION_GENERATION",self.session_generation)):
                strong(name,value,8 if name=="SELLER_USERNAME" else 32)
            if not self.session_cookie_secure: raise ValueError("SESSION_COOKIE_SECURE=true is required")
            try:
                keys=(self.encryption_key,)+self.historical_encryption_keys
                decoded=[base64.urlsafe_b64decode(value.encode("ascii")) for value in keys]
            except Exception as exc: raise ValueError("Token encryption keys must be URL-safe base64") from exc
            if any(len(value)!=32 for value in decoded) or len(set(keys))!=len(keys):
                raise ValueError("Token encryption keys must be distinct Fernet keys")
        origin=urlsplit(self.canonical_origin)
        if self.canonical_origin and (origin.scheme!="https" or not origin.netloc or origin.username or origin.password or origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError("CANONICAL_ORIGIN must be one credential-free HTTPS origin")
        if strict and not self.canonical_origin: raise ValueError("CANONICAL_ORIGIN is required")
        for name,value in (("STRIPE_SUCCESS_URL",self.stripe_success_url),("STRIPE_CANCEL_URL",self.stripe_cancel_url)):
            if value.count("{ORDER_ID}") != 1: raise ValueError(f"{name} must contain the exact {{ORDER_ID}} placeholder once")
            if strict and urlsplit(value).scheme!="https": raise ValueError(f"{name} must use HTTPS")
        if self.app_environment=="production":
            strong("PRODUCTION_PROVIDER_ACTIVATION_ID",self.production_provider_activation_id)
            strong("EXPECTED_STRIPE_ACCOUNT_ID",self.expected_stripe_account_id,8); strong("EXPECTED_EBAY_SELLER_ID",self.expected_ebay_seller_id,6)
            if not self.rate_limit_storage_uri or self.rate_limit_storage_uri=="memory://": raise ValueError("Production requires a shared rate-limit store")
            for name,path in (("DATABASE_PATH",self.database_path),("UPLOAD_DIRECTORY",self.upload_directory),("BACKUP_DIRECTORY",self.backup_directory)):
                if not Path(path).is_absolute() or not str(Path(path)).startswith("/data/"): raise ValueError(f"{name} must be a production persistent path")

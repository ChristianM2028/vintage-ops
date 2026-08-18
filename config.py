import os
from dataclasses import dataclass

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
    max_upload_size: int = int(os.getenv("MAX_UPLOAD_SIZE", str(8 * 1024 * 1024)))
    permitted_image_extensions: tuple = tuple(x.strip().lower().lstrip(".") for x in os.getenv("PERMITTED_IMAGE_EXTENSIONS", "jpg,jpeg,png,gif,webp").split(",") if x.strip())
    max_photo_count: int = int(os.getenv("MAX_PHOTO_COUNT", "10"))
    media_public_base_url: str = os.getenv("MEDIA_PUBLIC_BASE_URL", "")
    media_upload_url: str = os.getenv("MEDIA_UPLOAD_URL", "")
    media_upload_token: str = os.getenv("MEDIA_UPLOAD_TOKEN", "")
    metadata_cache_seconds: int = int(os.getenv("EBAY_METADATA_CACHE_SECONDS", "86400"))

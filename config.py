import os
from dataclasses import dataclass

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

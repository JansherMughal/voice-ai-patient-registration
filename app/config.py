"""Environment-driven settings. No secrets hardcoded — all read from env vars."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./patients.db")

# Shared secret Vapi sends back in the X-Vapi-Secret header on every webhook
# call, so random internet traffic can't trigger patient writes.
VAPI_WEBHOOK_SECRET = os.environ.get("VAPI_WEBHOOK_SECRET", "")

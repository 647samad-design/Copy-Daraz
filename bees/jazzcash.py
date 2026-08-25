"""
JazzCash Page Redirection (HostedCheckout) integration.

Docs: https://sandbox.jazzcash.com.pk/Sandbox/  (merchant guide PDF, section
"Mobile Account - Page Redirection API"). This module only builds/verifies
the signed request - the actual redirect + callback handling lives in
views.py (initiate_jazzcash_payment / jazzcash_return).

To go live you need a JazzCash merchant account, which gives you three
values to put in your .env:
  JAZZCASH_MERCHANT_ID
  JAZZCASH_PASSWORD
  JAZZCASH_INTEGRITY_SALT
Until those are set, JAZZCASH_ENABLED below is False and the "JazzCash"
option simply won't be offered at checkout - nothing breaks.
"""
import hashlib
import hmac
from datetime import datetime, timedelta

from django.conf import settings

SANDBOX_URL = "https://sandbox.jazzcash.com.pk/CustomerPortal/transactionmanagement/merchantform/"
PRODUCTION_URL = "https://payments.jazzcash.com.pk/CustomerPortal/transactionmanagement/merchantform/"


def is_configured():
    return bool(settings.JAZZCASH_MERCHANT_ID and settings.JAZZCASH_PASSWORD and settings.JAZZCASH_INTEGRITY_SALT)


def checkout_url():
    return SANDBOX_URL if settings.JAZZCASH_SANDBOX else PRODUCTION_URL


def _secure_hash(params: dict) -> str:
    """JazzCash's signature scheme: sort all pp_* fields alphabetically by
    key, join their values with '&', prefix the integrity salt, then
    HMAC-SHA256 the result using the integrity salt as the key."""
    sorted_values = "&".join(str(params[k]) for k in sorted(params) if params[k] not in (None, ""))
    message = f"{settings.JAZZCASH_INTEGRITY_SALT}&{sorted_values}"
    return hmac.new(
        settings.JAZZCASH_INTEGRITY_SALT.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def build_payment_request(order, return_url: str) -> dict:
    """Returns the full dict of form fields to POST (as an auto-submitting
    form) to JazzCash's checkout URL for this order."""
    now = datetime.now()
    txn_ref = f"T{order.id}{now.strftime('%Y%m%d%H%M%S')}"
    amount_paisa = int(order.total * 100)  # JazzCash expects amount in paisa, no decimal point

    params = {
        "pp_Version": "1.1",
        "pp_TxnType": "MWALLET",
        "pp_Language": "EN",
        "pp_MerchantID": settings.JAZZCASH_MERCHANT_ID,
        "pp_Password": settings.JAZZCASH_PASSWORD,
        "pp_TxnRefNo": txn_ref,
        "pp_Amount": str(amount_paisa),
        "pp_TxnCurrency": "PKR",
        "pp_TxnDateTime": now.strftime("%Y%m%d%H%M%S"),
        "pp_BillReference": f"order{order.id}",
        "pp_Description": f"19Bees order #{order.id}",
        "pp_TxnExpiryDateTime": (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S"),
        "pp_ReturnURL": return_url,
        "pp_SecureHash": "",
    }
    params["pp_SecureHash"] = _secure_hash(params)
    return params, txn_ref


def verify_response(response_data: dict) -> bool:
    """Recomputes the hash on JazzCash's callback data and compares it to
    the pp_SecureHash they sent, so we know the response wasn't tampered
    with in transit."""
    received_hash = response_data.get("pp_SecureHash", "")
    check_params = {k: v for k, v in response_data.items() if k != "pp_SecureHash"}
    expected_hash = _secure_hash(check_params)
    return hmac.compare_digest(received_hash, expected_hash)


def is_success_response(response_data: dict) -> bool:
    """JazzCash uses pp_ResponseCode '000' for success."""
    return response_data.get("pp_ResponseCode") == "000"

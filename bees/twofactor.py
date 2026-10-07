"""Two-step sign-in with an authenticator app (Google Authenticator,
Microsoft Authenticator, 1Password, Authy ...): standard 6-digit TOTP codes
(RFC 6238, 30-second steps), plus one-time backup codes."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings

STEP = 30
DIGITS = 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((now or time.time()) // STEP)


def verify(secret, code, last_step=0, now=None):
    """Returns the matched time step (to store, so a code can't be used
    twice) or None. Accepts one step either side for clock drift."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if not secret or len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in (step - 1, step, step + 1):
        if candidate > (last_step or 0) and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret, account, issuer):
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_svg(data):
    """QR code as inline SVG markup."""
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    return img.to_string(encoding="unicode")


def _hash_code(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.replace("-", "").lower().encode(), hashlib.sha256).hexdigest()


def new_backup_codes(count=10):
    """Returns (codes to show once, hashes to store)."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]
    return codes, [_hash_code(c) for c in codes]


def use_backup_code(stored_hashes, code):
    """Returns the remaining hashes if ``code`` matched one, else None."""
    digest = _hash_code(code.strip())
    for h in stored_hashes or []:
        if hmac.compare_digest(h, digest):
            return [x for x in stored_hashes if x != h]
    return None

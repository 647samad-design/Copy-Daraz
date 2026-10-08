"""Small security helpers shared by the views."""
import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next_url(request, candidate, fallback="home"):
    """Returns ``candidate`` only if it points back to this site, otherwise
    ``fallback``. Prevents open redirects like ?next=https://evil.example."""
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def redirect_back(request, fallback="home", param="next"):
    """Redirects to ?next= / POST next / Referer if it's on this site."""
    candidate = request.POST.get(param) or request.GET.get(param) or request.META.get("HTTP_REFERER")
    return redirect(safe_next_url(request, candidate, fallback))


def client_ip(request):
    """The client's IP address for rate limiting.

    X-Forwarded-For is only trusted when the app is told how many reverse
    proxies sit in front of it (settings.NUM_PROXIES, e.g. 1 on Render,
    Railway or Heroku). Otherwise anyone could send a fake header and get
    a fresh rate-limit bucket on every request."""
    num_proxies = getattr(settings, "NUM_PROXIES", 0)
    if num_proxies:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= num_proxies:
            return parts[-num_proxies]
    return request.META.get("REMOTE_ADDR", "unknown")


IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
DOCUMENT_EXTENSIONS = IMAGE_EXTENSIONS | {"pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # phone photos; they are resized on save


def _extension(name):
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def validate_image_upload(uploaded):
    """Accepts only real raster images (checked with Pillow, not just by
    file name) up to 5 MB. Blocks HTML/SVG uploads that could carry
    scripts and run on your domain."""
    if uploaded is None:
        return None
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValidationError("Images must be 10 MB or smaller.")
    ext = _extension(uploaded.name)
    if ext in ("heic", "heif"):
        raise ValidationError("iPhone HEIC photos can't be shown on the web. On the iPhone choose Settings > Camera > Formats > "
                              "Most Compatible, or export the photo as JPG, then upload it again.")
    if ext not in IMAGE_EXTENSIONS:
        raise ValidationError("Please upload a JPG, PNG, WebP or GIF image.")
    try:
        from PIL import Image
        uploaded.seek(0)
        with Image.open(uploaded) as img:
            img.verify()
    except Exception:
        raise ValidationError("That file doesn't look like a valid image.")
    finally:
        uploaded.seek(0)
    return uploaded


def validate_document_upload(uploaded):
    """Seller verification documents: images or PDF, up to 5 MB."""
    if uploaded is None:
        return None
    ext = _extension(uploaded.name)
    if ext in IMAGE_EXTENSIONS:
        return validate_image_upload(uploaded)
    if ext != "pdf":
        raise ValidationError("Documents must be a PDF or an image (JPG, PNG, WebP).")
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValidationError("Documents must be 5 MB or smaller.")
    uploaded.seek(0)
    if uploaded.read(5) != b"%PDF-":
        uploaded.seek(0)
        raise ValidationError("That file doesn't look like a valid PDF.")
    uploaded.seek(0)
    return uploaded


def random_upload_name(folder, uploaded):
    return f"{folder}/{uuid.uuid4().hex}.{_extension(uploaded.name) or 'bin'}"

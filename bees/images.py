"""Prepares uploaded pictures for the web.

Phone photos are often 3-10 MB, sideways (the camera stores the rotation
in EXIF instead of turning the pixels) and carry the GPS location the
photo was taken at. Before saving, every uploaded image is:

  * turned the right way up (EXIF orientation applied),
  * scaled down so its longest side is at most ``max_side`` pixels,
  * re-saved compressed (JPEG for photos, PNG when it has transparency),
  * stripped of EXIF / GPS data.

Animated GIFs are kept as they are. If anything goes wrong the original
file is used, so an upload never fails because of this step.
"""
import io
import logging
import os

from django.core.files.uploadedfile import SimpleUploadedFile

logger = logging.getLogger(__name__)

PRODUCT_MAX = 1600
LOGO_MAX = 600
BANNER_MAX = 2000


def optimize(uploaded, max_side=PRODUCT_MAX):
    """Returns a new uploaded-file object ready to save (or ``uploaded``
    itself if it can't be processed)."""
    if uploaded is None:
        return None
    try:
        from PIL import Image, ImageOps
        uploaded.seek(0)
        img = Image.open(uploaded)
        if getattr(img, "is_animated", False) and img.n_frames > 1:
            uploaded.seek(0)
            return uploaded
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        if has_alpha:
            img.save(out, "PNG", optimize=True)
            ext, ctype = "png", "image/png"
        else:
            img.save(out, "JPEG", quality=85, optimize=True, progressive=True)
            ext, ctype = "jpg", "image/jpeg"
        data = out.getvalue()
        base = os.path.splitext(os.path.basename(uploaded.name or "image"))[0][:60] or "image"
        return SimpleUploadedFile(f"{base}.{ext}", data, content_type=ctype)
    except Exception:
        logger.exception("Could not optimise uploaded image %s", getattr(uploaded, "name", ""))
        uploaded.seek(0)
        return uploaded


def save_public(uploaded, folder="products", max_side=PRODUCT_MAX):
    """Optimises and stores an image in public storage (this server or
    Supabase Storage). Returns its URL."""
    from django.core.files.storage import default_storage
    from .security import random_upload_name
    ready = optimize(uploaded, max_side)
    path = default_storage.save(random_upload_name(folder, ready), ready)
    return default_storage.url(path)

"""Things the store owner must know about: failed emails, failed refunds,
refunds/disputes made in Stripe. Shown as admin notifications and kept in
the audit log (Admin > System check lists the recent ones)."""
import logging

logger = logging.getLogger(__name__)

EMAIL_FAILED = "Email failed"
REFUND_FAILED = "Refund failed"
STRIPE_EVENT = "Stripe"


def log(action):
    from .models import AuditLog
    try:
        AuditLog.objects.create(user=None, action=action[:255])
    except Exception:
        logger.exception("Could not write audit log entry")


def notify_staff(message, link="/manage/", category=None):
    from django.contrib.auth import get_user_model
    from .models import Notification
    if category:
        log(f"{category}: {message}")
    for user in get_user_model().objects.filter(is_staff=True, is_active=True):
        Notification.objects.create(user=user, message=message[:255], link=link)


def email_failed(subject, recipient, exc):
    logger.error("Email '%s' to %s failed: %s", subject, recipient, exc)
    log(f"{EMAIL_FAILED}: '{subject}' to {recipient} - {type(exc).__name__}: {exc}")

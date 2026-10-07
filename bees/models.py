import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

hex_color = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Enter a 6-digit hex colour like #0E3B43.")


def _private_storage():
    """Storage for sensitive seller documents (ID scans, business
    certificates). Points at the private Supabase bucket in production,
    and at a non-public folder locally - never at the public media URL."""
    from django.core.files.storage import storages
    return storages["private"]


def _public_upload_path(folder):
    def _path(instance, filename):
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        return f"{folder}/{uuid.uuid4().hex}.{ext}"
    _path.__name__ = f"upload_to_{folder.replace('/', '_')}"
    return _path


def seller_cert_path(instance, filename):
    return _public_upload_path("seller_docs/certificates")(instance, filename)


def seller_id_path(instance, filename):
    return _public_upload_path("seller_docs/ids")(instance, filename)


def seller_logo_path(instance, filename):
    return _public_upload_path("seller_docs/logos")(instance, filename)


def seller_banner_path(instance, filename):
    return _public_upload_path("seller_docs/banners")(instance, filename)


class ProductQuerySet(models.QuerySet):
    def live(self):
        """Products customers can see and buy: approved by the store, and
        the seller (if any) isn't pending, rejected or suspended."""
        return self.filter(approval_status="approved").filter(
            models.Q(seller_account__isnull=True) | models.Q(seller_account__status="approved")
        )


class Product(models.Model):
    CATEGORY_CHOICES = [
        ("skincare", "Skin care"),
        ("haircare", "Hair care"),
        ("grocery", "Grocery"),
        ("fashion", "Fashion"),
        ("electronics", "Electronics"),
        ("3d-printers", "3D printers"),
        ("pasta-tools", "Pasta, Noodle & Pizza Tools"),
        ("sim-devices", "SIM devices"),
        ("screen-protector", "Screen protector"),
        ("casserole-pot", "Casserole pot"),
        ("table-lamp", "Table lamp"),
        ("hoodies", "Hoodies & Sweatshirts"),
        ("toy-boxes", "Toy boxes and organizers"),
        ("sneakers", "Sneakers"),
        ("education", "Education"),
        ("dress-up-kits", "Dress-Up Kits"),
        ("microphones", "Microphones"),
        ("leashes", "Leashes and harnesses"),
        ("donate-education", "Donate to education"),
        ("coloring-drawing", "Coloring & Drawing"),
        ("lotion-cream", "Lotion, Cream and Scrubs"),
    ]

    name = models.CharField(max_length=255)
    image_url = models.CharField(max_length=500)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    old_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_percent = models.PositiveIntegerField(default=0)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="grocery", db_index=True)
    description = models.TextField(blank=True)
    is_flash_sale = models.BooleanField(default=False, db_index=True)
    stock = models.PositiveIntegerField(default=50)
    seller_name = models.CharField(max_length=100, default="Official Store", db_index=True)
    seller_account = models.ForeignKey("SellerAccount", related_name="products", on_delete=models.SET_NULL, null=True, blank=True)
    APPROVAL_CHOICES = [
        ("approved", "Approved"),
        ("pending", "Pending review"),
        ("rejected", "Rejected"),
    ]
    approval_status = models.CharField(max_length=20, choices=APPROVAL_CHOICES, default="approved", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["approval_status", "category"]),
            models.Index(fields=["approval_status", "is_flash_sale"]),
            models.Index(fields=["stock"]),
        ]


    objects = ProductQuerySet.as_manager()

    @property
    def is_live(self):
        if self.approval_status != "approved":
            return False
        return not self.seller_account_id or self.seller_account.status == "approved"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_stock = None
        if not is_new:
            old_stock = Product.objects.filter(pk=self.pk).values_list("stock", flat=True).first()
        super().save(*args, **kwargs)
        # Notify the seller once when stock crosses into the low-stock zone,
        # not on every save while it stays low.
        if not is_new and old_stock is not None and self.seller_account_id:
            crossed_low = old_stock > 5 and 0 < self.stock <= 5
            crossed_out = old_stock > 0 and self.stock <= 0
            if crossed_out:
                Notification.objects.create(
                    user=self.seller_account.user,
                    message=f"'{self.name}' is now out of stock. Restock it to keep selling.",
                    link="/seller/dashboard/",
                )
            elif crossed_low:
                Notification.objects.create(
                    user=self.seller_account.user,
                    message=f"'{self.name}' is running low ({self.stock} left). Consider restocking soon.",
                    link="/seller/dashboard/",
                )

    def __str__(self):
        return self.name

    @property
    def average_rating(self):
        if hasattr(self, "avg_rating"):
            return round(self.avg_rating, 1) if self.avg_rating else 0
        reviews = self.reviews.all()
        if not reviews:
            return 0
        return round(sum(r.rating for r in reviews) / len(reviews), 1)

    @property
    def rating_count(self):
        if hasattr(self, "review_count"):
            return self.review_count
        return self.reviews.count()


class Review(models.Model):
    product = models.ForeignKey(Product, related_name="reviews", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="reviews")
    username = models.CharField(max_length=100)
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField()
    is_verified_purchase = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.username} on {self.product.name}"


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey("auth.User", related_name="orders", on_delete=models.CASCADE, null=True, blank=True)
    guest_email = models.EmailField(blank=True)
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=150)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField("State / Province / Region", max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2 country code, e.g. US")
    phone = models.CharField(max_length=30)
    PAYMENT_METHOD_CHOICES = [
        ("card", "Card (Stripe)"),
        ("cod", "Cash on delivery"),
    ]
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHOD_CHOICES, default="card")
    PAYMENT_STATUS_CHOICES = [
        ("not_applicable", "Not applicable (COD)"),
        ("pending", "Awaiting payment"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    ]
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="not_applicable", db_index=True)
    stripe_session_id = models.CharField(max_length=255, blank=True, db_index=True)
    # True while a cash-on-delivery order is being paid online in advance:
    # if that payment is abandoned, the order goes back to cash on delivery
    # instead of being cancelled.
    cod_fallback = models.BooleanField(default=False)
    stripe_payment_intent = models.CharField(max_length=255, blank=True)
    currency = models.CharField(max_length=3, default="usd")
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    coupon_code = models.CharField(max_length=30, blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tracking_number = models.CharField(max_length=60, blank=True)
    courier_name = models.CharField(max_length=60, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    # Store credit spent on this order (a payment method, so it doesn't
    # change what the goods cost) and whether it was given back on cancel.
    credit_used = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_returned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    CANCELLABLE_STATUSES = ("pending", "confirmed")

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    @property
    def is_cancellable(self):
        return self.status in self.CANCELLABLE_STATUSES

    @property
    def subtotal(self):
        return sum((item.subtotal for item in self.items.all()), 0)

    @property
    def grand_total(self):
        """Order value: items - discount + shipping + tax."""
        return max(self.subtotal - self.discount_amount, 0) + self.shipping_amount + self.tax_amount

    @property
    def total(self):
        """What the customer pays by card / cash: order value minus any
        store credit used."""
        return max(self.grand_total - self.credit_used, 0)

    @property
    def goods_paid(self):
        """What the items cost after the coupon, including their tax -
        the basis for refunding returned items."""
        return max(self.subtotal - self.discount_amount, 0) + self.tax_amount

    def refund_value(self, item):
        """Fair refund for one returned item: its share of what was paid
        for the goods (so a coupon discount isn't refunded as cash)."""
        from decimal import Decimal, ROUND_HALF_UP
        subtotal = Decimal(self.subtotal)
        if not subtotal:
            return Decimal("0.00")
        value = Decimal(self.goods_paid) * Decimal(item.subtotal) / subtotal
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def return_deadline(self):
        from datetime import timedelta
        delivered = self.delivered_at or self.created_at
        return delivered + timedelta(days=SiteSettings.load().return_days)

    @property
    def can_return(self):
        from django.utils import timezone
        return self.status == "delivered" and timezone.now() <= self.return_deadline()

    @property
    def total_cents(self):
        from .payments import to_cents
        return to_cents(self.total, self.currency)

    @property
    def contact_email(self):
        if self.email:
            return self.email
        if self.user and self.user.email:
            return self.user.email
        return self.guest_email

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        if not is_new:
            old_status = Order.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if self.status == "delivered" and old_status != "delivered" and not self.delivered_at:
            from django.utils import timezone
            self.delivered_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["delivered_at"]
        super().save(*args, **kwargs)
        if not is_new and old_status and old_status != self.status:
            from . import order_emails
            order_emails.status_changed(self, old_status, self.status)
        if self.user and not is_new and old_status and old_status != self.status:
            Notification.objects.create(
                user=self.user,
                message=f"Order #{self.id} is now {self.get_status_display()}.",
                link=f"/my-orders/",
            )
            if self.status == "delivered" and old_status != "delivered":
                _reward_referrer_for(self.user)
                # 1 loyalty point per whole unit of currency spent.
                points_earned = int(self.total)
                if points_earned > 0:
                    profile, _ = Profile.objects.get_or_create(user=self.user)
                    profile.loyalty_points += points_earned
                    profile.save(update_fields=["loyalty_points"])
                    Notification.objects.create(
                        user=self.user,
                        message=f"You earned {points_earned} loyalty points from order #{self.id}!",
                        link="/profile/",
                    )

    def __str__(self):
        return f"Order #{self.id} - {self.user.username if self.user else self.contact_email}"


def _reward_referrer_for(user):
    """Gives the person who referred ``user`` a one-time 10% coupon, the
    first time one of ``user``'s orders is delivered."""
    import secrets
    from datetime import timedelta
    from django.utils import timezone
    updated = Profile.objects.filter(user=user, referral_rewarded=False).exclude(referred_by="").update(referral_rewarded=True)
    if not updated:
        return
    ref = Profile.objects.filter(user=user).values_list("referred_by", flat=True).first()
    referrer = Profile.objects.filter(referral_code=ref).exclude(user=user).select_related("user").first()
    if not referrer:
        return
    code = "REF-" + secrets.token_hex(3).upper()
    Coupon.objects.create(code=code, percent_off=10, usage_limit=1, per_user_limit=1,
                          expiry_date=timezone.localdate() + timedelta(days=90))
    Notification.objects.create(
        user=referrer.user, link="/profile/",
        message=f"{user.username} made their first purchase with your referral link! Here's 10% off your next order: {code}",
    )


class OrderItemQuerySet(models.QuerySet):
    def counted(self):
        """Items that count as real sales: the order wasn't cancelled, its
        payment didn't fail or get refunded, and the item wasn't refunded
        through a return."""
        return (
            self.exclude(order__status="cancelled")
            .exclude(order__payment_status__in=["pending", "failed", "refunded"])
            .exclude(return_requests__status="refunded")
        )


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    # Recorded when the order is placed, so later rate changes or product
    # deletions never rewrite past earnings.
    seller_account = models.ForeignKey(
        "SellerAccount", related_name="sold_items", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Empty for the store's own products (no commission).",
    )
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    objects = OrderItemQuerySet.as_manager()
    FULFILLMENT_CHOICES = [
        ("pending", "Pending"),
        ("packed", "Packed"),
        ("handed_to_courier", "Handed to courier"),
        ("delivered", "Delivered"),
    ]
    fulfillment_status = models.CharField(max_length=20, choices=FULFILLMENT_CHOICES, default="pending")

    @property
    def subtotal(self):
        return self.price * self.quantity

    @property
    def seller_earning(self):
        return self.subtotal - self.commission_amount

    def apply_commission(self, seller):
        """Sets seller and commission from the seller's current rate."""
        from decimal import Decimal, ROUND_HALF_UP
        self.seller_account = seller
        rate = Decimal(str(seller.effective_commission_rate)) if seller else Decimal("0")
        self.commission_rate = rate
        self.commission_amount = (self.price * self.quantity * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if seller is not None:
            seller.__dict__.pop("_totals_cache", None)  # totals change once this item is saved

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"


class Wishlist(models.Model):
    user = models.ForeignKey("auth.User", related_name="wishlist_items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "product")

    def __str__(self):
        return f"{self.user.username} ♥ {self.product.name}"


class Coupon(models.Model):
    code = models.CharField(max_length=30, unique=True)
    percent_off = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text="Coupon stops working after this date. Leave blank for no expiry.",
    )
    usage_limit = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum number of times this code can be used in total, across all customers. Leave blank for unlimited.",
    )
    per_user_limit = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of times a single customer can use this code.",
    )
    min_order_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Cart total must be at least this much for the code to apply. 0 = no minimum.",
    )

    def __str__(self):
        return f"{self.code} (-{self.percent_off}%)"

    def times_used(self):
        return Order.objects.filter(coupon_code__iexact=self.code).exclude(status="cancelled").count()

    def times_used_by(self, user):
        if not user or not user.is_authenticated:
            return 0
        return Order.objects.filter(
            user=user, coupon_code__iexact=self.code
        ).exclude(status="cancelled").count()

    def is_valid_for(self, user, order_total):
        """Returns (is_valid, error_message). error_message is None if valid."""
        from django.utils import timezone

        if not self.active:
            return False, "This coupon is no longer active."
        if self.expiry_date and timezone.localdate() > self.expiry_date:
            return False, "This coupon has expired."
        if order_total < self.min_order_value:
            from .templatetags.bees_extras import money
            return False, f"This coupon needs a minimum order of {money(self.min_order_value)}."
        if self.usage_limit is not None and self.times_used() >= self.usage_limit:
            return False, "This coupon has reached its usage limit."
        if self.times_used_by(user) >= self.per_user_limit:
            return False, "You've already used this coupon the maximum number of times."
        return True, None


class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name="extra_images", on_delete=models.CASCADE)
    image_url = models.CharField(max_length=500)

    def __str__(self):
        return f"Image for {self.product.name}"


class Profile(models.Model):
    user = models.OneToOneField("auth.User", related_name="profile", on_delete=models.CASCADE)
    phone = models.CharField(max_length=30, blank=True)
    email_verified = models.BooleanField(default=False)
    referral_code = models.CharField(max_length=12, unique=True, blank=True)
    referred_by = models.CharField(max_length=12, blank=True)
    # The referrer is rewarded once, when this customer's first order is
    # delivered (so fake sign-ups earn nothing).
    referral_rewarded = models.BooleanField(default=False)
    loyalty_points = models.PositiveIntegerField(default=0)
    store_credit = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Stripe customer that holds this person's saved cards. Card numbers
    # never touch our servers - Stripe stores them.
    stripe_customer_id = models.CharField(max_length=255, blank=True)

    POINTS_PER_UNIT = 100  # 100 reward points = 1.00 of store credit

    def __str__(self):
        return f"{self.user.username}'s profile"


class Address(models.Model):
    user = models.ForeignKey("auth.User", related_name="addresses", on_delete=models.CASCADE)
    label = models.CharField(max_length=30, default="Home")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Addresses"
        ordering = ["-is_default", "id"]

    def __str__(self):
        return f"{self.label} - {self.user.username}"


class Question(models.Model):
    product = models.ForeignKey(Product, related_name="questions", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="questions")
    username = models.CharField(max_length=100)
    question = models.TextField()
    answer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Q on {self.product.name}: {self.question[:40]}"


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email


class Notification(models.Model):
    user = models.ForeignKey("auth.User", related_name="notifications", on_delete=models.CASCADE)
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return self.message


class SearchLog(models.Model):
    query = models.CharField(max_length=150, unique=True)
    count = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-count"]

    def __str__(self):
        return f"{self.query} ({self.count})"


class SellerAccount(models.Model):
    ACCOUNT_TYPE_CHOICES = [
        ("individual", "Individual seller"),
        ("organization", "Organization / Business"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("suspended", "Suspended"),
    ]

    user = models.OneToOneField("auth.User", related_name="seller_account", on_delete=models.CASCADE)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default="individual", db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    total_paid_out = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Total amount already paid to this seller (recorded by admin after each payout)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Registration details (per marketplace onboarding spec)
    full_name = models.CharField(max_length=150, blank=True)
    business_name = models.CharField(max_length=150, blank=True)
    organization_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    cnic = models.CharField("National ID / Passport number", max_length=30, blank=True)
    business_address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    store_description = models.TextField(blank=True)
    product_categories = models.CharField(max_length=255, blank=True, help_text="Comma-separated categories the store will sell")
    brand_info = models.TextField(blank=True)
    tax_info = models.CharField(max_length=100, blank=True)
    bank_details = models.CharField(max_length=255, blank=True)

    business_certificate = models.FileField(upload_to=seller_cert_path, storage=_private_storage, blank=True, null=True)
    id_document = models.FileField(upload_to=seller_id_path, storage=_private_storage, blank=True, null=True)
    store_logo = models.ImageField(upload_to=seller_logo_path, blank=True, null=True)
    store_banner = models.ImageField(upload_to=seller_banner_path, blank=True, null=True)

    admin_note = models.CharField(max_length=255, blank=True, help_text="Internal note, e.g. reason for rejection or requested info")

    def save(self, *args, **kwargs):
        if self.pk is None and not kwargs.get("update_fields"):
            self.commission_rate = 20 if self.account_type == "organization" else 10
        super().save(*args, **kwargs)

    def _totals(self):
        if not hasattr(self, "_totals_cache"):
            from django.db.models import Sum, F
            agg = OrderItem.objects.filter(seller_account=self).counted().aggregate(
                sales=Sum(F("price") * F("quantity")), commission=Sum("commission_amount"),
            )
            self._totals_cache = (agg["sales"] or 0, agg["commission"] or 0)
        return self._totals_cache

    @property
    def commission_total(self):
        """Commission the platform has earned from this seller's sales."""
        return self._totals()[1]

    @property
    def net_earnings(self):
        """Seller's total earnings after the platform's commission is deducted."""
        sales, commission = self._totals()
        return round(float(sales) - float(commission), 2)

    @property
    def amount_owed(self):
        """Net earnings not yet paid out to the seller. Never negative."""
        owed = float(self.net_earnings) - float(self.total_paid_out)
        return round(max(owed, 0), 2)

    @property
    def display_name(self):
        return self.business_name or self.organization_name or self.full_name or self.user.username

    @property
    def lifetime_sales(self):
        """Value of this seller's completed (not cancelled or refunded) sales."""
        return self._totals()[0]

    @property
    def effective_commission_rate(self):
        """
        Tiered commission: the more a seller sells, the lower their commission rate,
        rewarding high-volume sellers. Base rate is the individual/organization rate;
        thresholds reduce it as lifetime sales grow.
        """
        base = float(self.commission_rate)
        sales = float(self.lifetime_sales)
        discount = 0
        for threshold, reduction in getattr(settings, "COMMISSION_TIERS", []):
            if sales >= threshold:
                discount = reduction
                break
        return max(base - discount, 3)

    @property
    def average_rating(self):
        reviews = self.seller_reviews.all()
        if not reviews:
            return 0
        return round(sum(r.rating for r in reviews) / len(reviews), 1)

    def __str__(self):
        return f"{self.display_name} ({self.get_account_type_display()}, {self.status})"


class OrganizationMember(models.Model):
    """Lets an organization account grant additional team members access to
    its seller dashboard, product management, and order fulfillment -
    without sharing the main login."""
    ROLE_CHOICES = [
        ("admin", "Admin - full access, can manage team"),
        ("staff", "Staff - manage products & orders only"),
    ]
    organization = models.ForeignKey(SellerAccount, related_name="team_members", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", related_name="organization_memberships", on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="staff")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("organization", "user")

    def __str__(self):
        return f"{self.user.username} @ {self.organization.display_name} ({self.role})"


class SellerReview(models.Model):
    seller = models.ForeignKey(SellerAccount, related_name="seller_reviews", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("seller", "user")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} rated {self.seller.display_name} {self.rating}/5"


def _money(value):
    from .templatetags.bees_extras import money
    return money(value)


class ReturnRequest(models.Model):
    STATUS_CHOICES = [
        ("requested", "Requested"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("refunded", "Refunded"),
    ]
    REFUND_METHOD_CHOICES = [
        ("original_payment", "Original payment method"),
        ("store_credit", "Store credit"),
    ]
    order_item = models.ForeignKey("OrderItem", related_name="return_requests", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    reason = models.TextField()
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default="original_payment")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="requested", db_index=True)
    admin_note = models.CharField(max_length=255, blank=True)
    # Filled in when refunded: how much went back to the card and how much
    # became store credit.
    card_refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    credit_refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def refund_total(self):
        return self.card_refund_amount + self.credit_refund_amount

    def refund_plan(self, as_credit=False):
        """How this return would be refunded: {"value", "card", "credit",
        "manual"}. ``value`` is the item's share of what was actually paid.
        Card orders go back to the card (up to what's left on it; anything
        paid with store credit goes back as credit). Cash-on-delivery orders
        can't be refunded to a card: ``manual`` is the amount the store pays
        back itself (bank transfer / cash), unless store credit is chosen."""
        from decimal import Decimal
        order = self.order_item.order
        value = order.refund_value(self.order_item)
        plan = {"value": value, "card": Decimal("0"), "credit": Decimal("0"), "manual": Decimal("0")}
        if as_credit or self.refund_method == "store_credit":
            plan["credit"] = value
        elif order.payment_status == "paid" and order.stripe_payment_intent:
            already = sum(
                (r.card_refund_amount for r in ReturnRequest.objects.filter(
                    order_item__order=order, status="refunded").exclude(pk=self.pk)),
                Decimal("0"),
            )
            plan["card"] = min(value, max(order.total - already, Decimal("0")))
            plan["credit"] = value - plan["card"]
        else:
            plan["manual"] = value
        return plan

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        if not is_new:
            old_status = ReturnRequest.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        super().save(*args, **kwargs)
        if not is_new and old_status and old_status != self.status:
            messages_by_status = {
                "approved": f"Your return for '{self.order_item.product_name}' was approved. Refund is being processed via {self.get_refund_method_display()}.",
                "rejected": f"Your return request for '{self.order_item.product_name}' was rejected." + (f" Note: {self.admin_note}" if self.admin_note else ""),
                "refunded": self._refunded_message(),
            }
            msg = messages_by_status.get(self.status)
            if msg:
                Notification.objects.create(user=self.user, message=msg, link="/my-orders/")

    def _refunded_message(self):
        parts = []
        if self.card_refund_amount:
            parts.append(f"{_money(self.card_refund_amount)} to your card")
        if self.credit_refund_amount:
            parts.append(f"{_money(self.credit_refund_amount)} as store credit")
        how = " and ".join(parts) if parts else "as agreed with our support team"
        return f"Your refund for '{self.order_item.product_name}' has been issued: {how}."

    def __str__(self):
        return f"Return: {self.order_item.product_name} ({self.status})"


class ShippingZone(models.Model):
    """Shipping fee for a group of countries. A zone whose countries are
    "*" covers every country not listed in another zone ("rest of the
    world"). With no zones at all, the flat fee in Settings applies
    everywhere."""
    name = models.CharField(max_length=60, help_text="e.g. United States, Europe, Rest of world")
    countries = models.TextField(help_text="Two-letter country codes separated by commas (US, CA), or * for every other country.")
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    free_over = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                    help_text="Orders at or above this amount (after discounts) ship free. Empty = never free.")
    delivery_days = models.PositiveSmallIntegerField(null=True, blank=True,
                                                     help_text="Usual delivery time in business days. Empty = store default.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_rest_of_world(self):
        return self.countries.strip() == "*"

    @property
    def country_codes(self):
        return [c.strip().upper() for c in self.countries.replace("\n", ",").split(",") if c.strip() and c.strip() != "*"]

    def fee_for(self, amount):
        from decimal import Decimal
        if self.free_over is not None and amount >= self.free_over:
            return Decimal("0")
        return self.fee


class SiteSettings(models.Model):
    """A single-row table for site-wide settings, editable from the admin
    panel. Everything brand-related lives here so the store can be
    re-branded (white-labelled) without touching code."""
    # --- Brand ---
    site_name = models.CharField("Store name", max_length=100, default="Lumen Market")
    tagline = models.CharField(max_length=160, default="Thoughtfully chosen products, delivered worldwide.")
    logo_url = models.CharField(
        max_length=500, blank=True,
        help_text="Full URL of your logo (upload it to Supabase Storage or any image host). Leave blank to show the store name as text.",
    )
    logo_file = models.ImageField(upload_to="branding/", blank=True, null=True, help_text="Or upload a logo here (PNG/SVG/JPG).")
    favicon_url = models.CharField(max_length=500, blank=True)
    primary_color = models.CharField(max_length=7, default="#0E3B43", validators=[hex_color], help_text="Main brand colour (buttons, footer). Use a dark colour so white text stays readable. Hex, e.g. #0E3B43")
    accent_color = models.CharField(max_length=7, default="#F2B33D", validators=[hex_color], help_text="Accent colour (badges, highlights, announcement bar). Use a light/bright colour - dark text sits on it. Hex, e.g. #F2B33D")
    hero_title = models.CharField(max_length=120, default="Everyday essentials, beautifully curated")
    hero_subtitle = models.CharField(max_length=240, default="Shop trusted brands and independent sellers. Secure checkout, fast shipping and easy returns.")
    hero_image_url = models.CharField(max_length=500, blank=True)
    # --- Contact & social ---
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=40, blank=True)
    company_address = models.CharField(max_length=255, blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    twitter_url = models.URLField("X / Twitter URL", blank=True)
    youtube_url = models.URLField(blank=True)
    # --- Commerce ---
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Applied to the discounted subtotal at checkout. 0 = no tax line.")
    shipping_flat_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Flat shipping fee per order. 0 = free shipping.")
    free_shipping_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Orders at or above this subtotal ship free. 0 = disabled.")
    return_days = models.PositiveSmallIntegerField(
        default=14, help_text="How many days after delivery customers can request a return.",
    )
    delivery_days = models.PositiveSmallIntegerField(
        default=5, help_text="Usual delivery time in business days. Used for the 'Arrives by' date customers see and get emailed.",
    )
    allow_cash_on_delivery = models.BooleanField(default=True, help_text="Show 'Cash on delivery' at checkout. Card payments appear automatically once Stripe keys are set.")
    show_language_menu = models.BooleanField(default=False, help_text="Show the English / Urdu / Roman Urdu language switcher.")
    banner_text = models.CharField(
        max_length=200, blank=True,
        help_text="Shown as a site-wide announcement bar at the top of every page, e.g. 'Summer sale: 20% off everything'. Leave blank to hide it.",
    )
    banner_active = models.BooleanField(default=False)
    banner_link = models.CharField(max_length=300, blank=True, help_text="Optional URL the banner links to (e.g. a sale category page).")

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    CACHE_KEY = "site_settings:v1"

    @classmethod
    def load(cls):
        """Site settings are read on every page, so they're cached for a
        minute and cleared whenever they're saved."""
        from django.core.cache import cache
        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, 60)
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import cache
        cache.delete(self.CACHE_KEY)

    @property
    def logo(self):
        if self.logo_file:
            try:
                return self.logo_file.url
            except Exception:
                return ""
        return self.logo_url

    def shipping_for(self, subtotal):
        from decimal import Decimal
        if self.free_shipping_threshold and subtotal >= self.free_shipping_threshold:
            return Decimal("0")
        return Decimal(self.shipping_flat_fee or 0)


class AuditLog(models.Model):
    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} ({self.user})"


class ChatThread(models.Model):
    """One support-chat thread per logged-in user (guests get a
    session-based thread key). Staff reply to these from /admin/."""
    user = models.OneToOneField("auth.User", null=True, blank=True, on_delete=models.CASCADE, related_name="chat_thread")
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_resolved = models.BooleanField(default=False)

    def __str__(self):
        return f"Chat with {self.user or self.session_key}"


class ChatMessage(models.Model):
    SENDER_CHOICES = [("user", "User"), ("support", "Support")]

    thread = models.ForeignKey(ChatThread, related_name="messages", on_delete=models.CASCADE)
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES, default="user")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender}: {self.message[:40]}"


# Header role flags are cached; clear them when a seller account or team
# membership changes.
from django.db.models.signals import post_delete, post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver([post_save, post_delete], sender=ShippingZone)
def _clear_shipping_cache(sender, **kwargs):
    from .shipping import clear_cache
    clear_cache()


@receiver([post_save, post_delete], sender=SellerAccount)
@receiver([post_save, post_delete], sender=OrganizationMember)
def _clear_seller_flag(sender, instance, **kwargs):
    from django.core.cache import cache
    cache.delete(f"is_seller:{instance.user_id}")

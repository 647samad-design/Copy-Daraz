from django.db import models


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
    seller_name = models.CharField(max_length=100, default="19Bees Mall", db_index=True)
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
    full_name = models.CharField(max_length=150)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    phone = models.CharField(max_length=30)
    payment_method = models.CharField(max_length=30, default="cod")
    PAYMENT_STATUS_CHOICES = [
        ("not_applicable", "Not applicable (COD)"),
        ("pending", "Awaiting payment"),
        ("paid", "Paid"),
        ("failed", "Failed"),
    ]
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default="not_applicable", db_index=True)
    jazzcash_txn_ref = models.CharField(max_length=40, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    coupon_code = models.CharField(max_length=30, blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tracking_number = models.CharField(max_length=60, blank=True)
    courier_name = models.CharField(max_length=60, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)
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
    def total(self):
        subtotal = sum(item.subtotal for item in self.items.all())
        return max(subtotal - self.discount_amount, 0)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        if not is_new:
            old_status = Order.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        super().save(*args, **kwargs)
        if self.user and not is_new and old_status and old_status != self.status:
            Notification.objects.create(
                user=self.user,
                message=f"Order #{self.id} is now {self.get_status_display()}.",
                link=f"/my-orders/",
            )
            if self.status == "delivered" and old_status != "delivered":
                points_earned = int(self.total // 100)
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
        return f"Order #{self.id} - {self.user.username}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
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
            return False, f"This coupon needs a minimum order of Rs.{self.min_order_value}."
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
    loyalty_points = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.user.username}'s profile"


class Address(models.Model):
    user = models.ForeignKey("auth.User", related_name="addresses", on_delete=models.CASCADE)
    label = models.CharField(max_length=30, default="Home")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.label} - {self.user.username}"


class Question(models.Model):
    product = models.ForeignKey(Product, related_name="questions", on_delete=models.CASCADE)
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
    cnic = models.CharField("CNIC / National ID", max_length=30, blank=True)
    business_address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default="Pakistan")
    store_description = models.TextField(blank=True)
    product_categories = models.CharField(max_length=255, blank=True, help_text="Comma-separated categories the store will sell")
    brand_info = models.TextField(blank=True)
    tax_info = models.CharField(max_length=100, blank=True)
    bank_details = models.CharField(max_length=255, blank=True)

    business_certificate = models.FileField(upload_to="seller_docs/certificates/", blank=True, null=True)
    id_document = models.FileField(upload_to="seller_docs/ids/", blank=True, null=True)
    store_logo = models.ImageField(upload_to="seller_docs/logos/", blank=True, null=True)
    store_banner = models.ImageField(upload_to="seller_docs/banners/", blank=True, null=True)

    admin_note = models.CharField(max_length=255, blank=True, help_text="Internal note, e.g. reason for rejection or requested info")

    def save(self, *args, **kwargs):
        if self.pk is None and not kwargs.get("update_fields"):
            self.commission_rate = 20 if self.account_type == "organization" else 10
        super().save(*args, **kwargs)

    @property
    def net_earnings(self):
        """Seller's total earnings after the platform's commission is deducted."""
        sales = float(self.lifetime_sales)
        commission = sales * self.effective_commission_rate / 100
        return round(sales - commission, 2)

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
        from django.db.models import Sum, F
        result = OrderItem.objects.filter(product__seller_account=self).aggregate(
            total=Sum(F("price") * F("quantity"))
        )
        return result["total"] or 0

    @property
    def effective_commission_rate(self):
        """
        Tiered commission: the more a seller sells, the lower their commission rate,
        rewarding high-volume sellers. Base rate is the individual/organization rate;
        thresholds reduce it as lifetime sales grow.
        """
        base = float(self.commission_rate)
        sales = float(self.lifetime_sales)
        if sales >= 200000:
            discount = 5
        elif sales >= 50000:
            discount = 2
        else:
            discount = 0
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


class ReturnRequest(models.Model):
    STATUS_CHOICES = [
        ("requested", "Requested"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("refunded", "Refunded"),
    ]
    REFUND_METHOD_CHOICES = [
        ("original_payment", "Original payment method"),
        ("wallet_credit", "19Bees wallet credit"),
    ]
    order_item = models.ForeignKey("OrderItem", related_name="return_requests", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    reason = models.TextField()
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default="original_payment")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="requested", db_index=True)
    admin_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

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
                "refunded": f"Refund of Rs.{self.order_item.subtotal} for '{self.order_item.product_name}' has been issued via {self.get_refund_method_display()}.",
            }
            msg = messages_by_status.get(self.status)
            if msg:
                Notification.objects.create(user=self.user, message=msg, link="/my-orders/")

    def __str__(self):
        return f"Return: {self.order_item.product_name} ({self.status})"


class SiteSettings(models.Model):
    """A single-row table for site-wide settings, editable from the admin panel."""
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    site_name = models.CharField(max_length=100, default="19Bees")
    banner_text = models.CharField(
        max_length=200, blank=True,
        help_text="Shown as a site-wide announcement bar at the top of every page, e.g. 'Eid Sale: 20% off everything!'. Leave blank to hide it.",
    )
    banner_active = models.BooleanField(default=False)
    banner_link = models.CharField(max_length=300, blank=True, help_text="Optional URL the banner links to (e.g. a sale category page).")

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


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

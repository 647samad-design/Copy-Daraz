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
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="grocery")
    description = models.TextField(blank=True)
    is_flash_sale = models.BooleanField(default=False)
    stock = models.PositiveIntegerField(default=50)
    seller_name = models.CharField(max_length=100, default="19Bees Mall")
    seller_account = models.ForeignKey("SellerAccount", related_name="products", on_delete=models.SET_NULL, null=True, blank=True)
    APPROVAL_CHOICES = [
        ("approved", "Approved"),
        ("pending", "Pending review"),
        ("rejected", "Rejected"),
    ]
    approval_status = models.CharField(max_length=20, choices=APPROVAL_CHOICES, default="approved")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def average_rating(self):
        reviews = self.reviews.all()
        if not reviews:
            return 0
        return round(sum(r.rating for r in reviews) / len(reviews), 1)


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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    coupon_code = models.CharField(max_length=30, blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

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

    def __str__(self):
        return f"Order #{self.id} - {self.user.username}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

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

    def __str__(self):
        return f"{self.code} (-{self.percent_off}%)"


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
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default="individual")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
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
        return self.business_name or self.user.username

    @property
    def lifetime_sales(self):
        from django.db.models import Sum, F
        items = OrderItem.objects.filter(product__seller_account=self)
        total = 0
        for i in items:
            total += i.subtotal
        return total

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
    order_item = models.ForeignKey("OrderItem", related_name="return_requests", on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="requested")
    admin_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return: {self.order_item.product_name} ({self.status})"


class SiteSettings(models.Model):
    """A single-row table for site-wide settings, editable from the admin panel."""
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    site_name = models.CharField(max_length=100, default="19Bees")

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

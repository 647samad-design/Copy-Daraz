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
    image_url = models.URLField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    old_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_percent = models.PositiveIntegerField(default=0)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default="grocery")
    description = models.TextField(blank=True)
    is_flash_sale = models.BooleanField(default=False)
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
    ]

    user = models.ForeignKey("auth.User", related_name="orders", on_delete=models.CASCADE)
    full_name = models.CharField(max_length=150)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    phone = models.CharField(max_length=30)
    payment_method = models.CharField(max_length=30, default="cod")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total(self):
        return sum(item.subtotal for item in self.items.all())

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

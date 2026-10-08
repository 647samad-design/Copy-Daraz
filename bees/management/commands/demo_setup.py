"""Prepares a demo copy of the store: three demo accounts (shopper, seller,
store admin) with sample products and orders, so visitors can try every
part of the marketplace with one click from the sign-in page.

    python manage.py demo_setup            # create / refresh the demo
    python manage.py demo_setup --reset    # also put the demo accounts back
                                           # the way they started (run daily)

Only use this on a separate demo copy, with DEMO_MODE=True in .env.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from bees.models import (
    Order, OrderItem, Product, Profile, SellerAccount, SellerPlan, SiteSettings,
)

DEMO_USERS = {
    "shopper": {"username": "demo_shopper", "first_name": "Sam", "last_name": "Shopper", "email": "shopper@demo.invalid"},
    "seller": {"username": "demo_seller", "first_name": "Sara", "last_name": "Seller", "email": "seller@demo.invalid"},
    "admin": {"username": "demo_admin", "first_name": "Alex", "last_name": "Admin", "email": "admin@demo.invalid"},
}
DEMO_USERNAMES = {u["username"] for u in DEMO_USERS.values()}
STORE_NAME = "Demo Studio"

SELLER_PRODUCTS = [
    ("Handmade Ceramic Mug", "casserole-pot", "18.00", "Wheel-thrown stoneware\n350 ml\nDishwasher safe"),
    ("Linen Tote Bag", "fashion", "24.00", "Natural linen\nInner pocket\nFits a 13-inch laptop"),
    ("Soy Wax Candle - Cedar", "table-lamp", "21.50", "100% soy wax\nAbout 40 hours burn time\nCotton wick"),
    ("Kids Watercolor Starter Kit", "coloring-drawing", "16.00", "12 colours\n2 brushes\nNon-toxic paints"),
    ("Merino Wool Beanie", "hoodies", "29.00", "Soft merino wool\nOne size\nHand wash"),
]


class Command(BaseCommand):
    help = "Create (or reset) the demo accounts, products and orders."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Put the demo accounts back to how they started.")

    def handle(self, *args, **options):
        if not Product.objects.exists():
            call_command("seed_data", stdout=self.stdout)
        with transaction.atomic():
            users = {role: self._user(data, staff=(role == "admin")) for role, data in DEMO_USERS.items()}
            seller = self._seller(users["seller"])
            products = self._products(seller)
            if options["reset"] or not Order.objects.filter(user=users["shopper"]).exists():
                self._orders(users["shopper"], products)
        site = SiteSettings.load()
        if not site.setup_completed:
            site.setup_completed = True
            site.save()
        self.stdout.write(self.style.SUCCESS(
            "Demo ready: demo_shopper, demo_seller and demo_admin. Turn on DEMO_MODE=True to show the one-click logins."))

    def _user(self, data, staff=False):
        user, _ = User.objects.get_or_create(username=data["username"])
        user.first_name, user.last_name, user.email = data["first_name"], data["last_name"], data["email"]
        user.is_active = True
        user.is_staff = user.is_superuser = staff
        user.set_unusable_password()  # demo accounts only open through the demo buttons
        user.save()
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.email_verified = True
        profile.totp_enabled = False
        profile.totp_secret = ""
        profile.backup_codes = []
        profile.save()
        return user

    def _seller(self, user):
        seller = SellerAccount.objects.filter(user=user).first() or SellerAccount(user=user, account_type="individual")
        seller.status = "approved"
        seller.full_name = "Sara Seller"
        seller.business_name = STORE_NAME
        seller.city, seller.country = "Lisbon", "Portugal"
        seller.store_description = "Small-batch homeware and gifts, made by hand and shipped worldwide."
        seller.vacation_mode = False
        pro = SellerPlan.objects.filter(active=True, price__gt=0).order_by("price").first()
        if pro:
            seller.plan, seller.plan_expires_at = pro, timezone.now() + timedelta(days=365)
        seller.save()
        return seller

    def _products(self, seller):
        from bees.management.commands.seed_data import _demo_image
        out = []
        for name, category, price, notes in SELLER_PRODUCTS:
            product = Product.objects.filter(seller_account=seller, name=name).first()
            if not product:
                from bees.ai import builtin_listing
                product = Product.objects.create(
                    name=name, category=category, price=Decimal(price), stock=random.Random(name).randint(3, 40),
                    description=builtin_listing(name, category, notes), image_url=_demo_image(name, category),
                    seller_account=seller, seller_name=seller.display_name, approval_status="approved",
                )
            elif product.approval_status != "approved":
                product.approval_status = "approved"
                product.save(update_fields=["approval_status"])
            out.append(product)
        return out

    def _orders(self, shopper, products):
        Order.objects.filter(user=shopper).delete()
        rng = random.Random(7)
        now = timezone.now()
        plan = [(18, "delivered"), (14, "delivered"), (11, "delivered"), (8, "shipped"), (5, "shipped"),
                (3, "confirmed"), (1, "confirmed"), (0, "confirmed")]
        for days_ago, status in plan:
            order = Order.objects.create(
                user=shopper, email=shopper.email, full_name="Sam Shopper", address="12 Demo Street",
                city=rng.choice(["London", "Berlin", "Toronto", "Dubai", "New York"]), country=rng.choice(["GB", "DE", "CA", "AE", "US"]),
                phone="+10000000000", payment_method="card", payment_status="paid", status=status,
                sellers_notified=True,
            )
            for product in rng.sample(products, rng.randint(1, 2)):
                item = OrderItem(order=order, product=product, product_name=product.name, price=product.price,
                                 quantity=rng.randint(1, 3))
                item.apply_commission(product.seller_account)
                item.fulfillment_status = {"delivered": "delivered", "shipped": "handed_to_courier"}.get(status, "pending")
                item.save()
            created = now - timedelta(days=days_ago, hours=rng.randint(0, 10))
            Order.objects.filter(pk=order.pk).update(
                created_at=created, delivered_at=created + timedelta(days=4) if status == "delivered" else None)

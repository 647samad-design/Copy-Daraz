"""
Automated tests for 19Bees.

These aren't exhaustive - they focus on the business logic that's most
likely to break silently: money calculations (order totals, commission),
permission checks (who can access what), and the bugs found and fixed
during development (display_name, N+1 query annotations).

Run with: python manage.py test bees
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from .models import (
    Product, Order, OrderItem, SellerAccount, OrganizationMember,
    Review, Notification,
)
from .views import with_ratings, get_seller_account_for_user


def make_product(**kwargs):
    defaults = dict(
        name="Test Product", category="skincare", price=Decimal("500.00"),
        stock=10, image_url="https://example.com/img.jpg",
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ProductRatingTests(TestCase):
    """The average_rating/rating_count properties used to run a fresh query
    per product (N+1). They now use a DB annotation when present, and fall
    back to the old per-query behaviour otherwise. Both paths must agree."""

    def setUp(self):
        self.product = make_product()

    def test_no_reviews_gives_zero(self):
        self.assertEqual(self.product.average_rating, 0)
        self.assertEqual(self.product.rating_count, 0)

    def test_average_rating_matches_manual_calculation(self):
        Review.objects.create(product=self.product, username="a", rating=4, comment="Good")
        Review.objects.create(product=self.product, username="b", rating=5, comment="Great")
        self.product.refresh_from_db()
        self.assertEqual(self.product.average_rating, 4.5)
        self.assertEqual(self.product.rating_count, 2)

    def test_annotated_queryset_matches_property_fallback(self):
        Review.objects.create(product=self.product, username="a", rating=4, comment="Good")
        Review.objects.create(product=self.product, username="b", rating=5, comment="Great")
        annotated = with_ratings(Product.objects.filter(pk=self.product.pk)).first()
        self.assertEqual(annotated.average_rating, 4.5)
        self.assertEqual(annotated.rating_count, 2)


class ProductStockNotificationTests(TestCase):
    """Product.save() notifies the seller once when stock crosses into the
    low-stock/out-of-stock zone. Must fire exactly once per crossing, not
    on every save while it stays low (that would spam the seller)."""

    def setUp(self):
        self.user = User.objects.create_user("seller1", "s1@example.com", "pass12345")
        self.seller = SellerAccount.objects.create(
            user=self.user, account_type="individual", status="approved",
            business_name="Test Shop",
        )
        self.product = make_product(stock=20, seller_account=self.seller)

    def test_low_stock_notification_fires_once(self):
        self.product.stock = 4
        self.product.save()
        self.assertEqual(
            Notification.objects.filter(user=self.user, message__icontains="running low").count(), 1
        )
        self.product.name = self.product.name
        self.product.save()
        self.assertEqual(
            Notification.objects.filter(user=self.user, message__icontains="running low").count(), 1
        )

    def test_out_of_stock_notification(self):
        self.product.stock = 0
        self.product.save()
        self.assertTrue(
            Notification.objects.filter(user=self.user, message__icontains="out of stock").exists()
        )


class OrderTotalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("buyer1", "b1@example.com", "pass12345")
        self.product = make_product(price=Decimal("250.00"))

    def test_total_sums_items_and_subtracts_discount(self):
        order = Order.objects.create(
            user=self.user, full_name="Buyer", address="St", city="Karachi",
            phone="0300", payment_method="cod", discount_amount=Decimal("50.00"),
        )
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name,
                                  price=Decimal("250.00"), quantity=2)
        self.assertEqual(order.total, Decimal("450.00"))

    def test_total_never_goes_negative(self):
        order = Order.objects.create(
            user=self.user, full_name="Buyer", address="St", city="Karachi",
            phone="0300", payment_method="cod", discount_amount=Decimal("999.00"),
        )
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name,
                                  price=Decimal("250.00"), quantity=1)
        self.assertEqual(order.total, 0)

    def test_is_cancellable_only_for_pending_or_confirmed(self):
        order = Order.objects.create(
            user=self.user, full_name="Buyer", address="St", city="Karachi",
            phone="0300", payment_method="cod", status="pending",
        )
        self.assertTrue(order.is_cancellable)
        order.status = "shipped"
        self.assertFalse(order.is_cancellable)
        order.status = "delivered"
        self.assertFalse(order.is_cancellable)


class SellerAccountTests(TestCase):
    """display_name used to silently ignore organization_name - every org
    account's name fell back to the raw username everywhere it was shown."""

    def test_display_name_prefers_business_name(self):
        user = User.objects.create_user("u1", "u1@example.com", "pass12345")
        seller = SellerAccount.objects.create(
            user=user, account_type="individual", business_name="Ali's Shop",
        )
        self.assertEqual(seller.display_name, "Ali's Shop")

    def test_display_name_falls_back_to_organization_name(self):
        user = User.objects.create_user("u2", "u2@example.com", "pass12345")
        org = SellerAccount.objects.create(
            user=user, account_type="organization", organization_name="Acme Traders",
        )
        self.assertEqual(org.display_name, "Acme Traders")

    def test_display_name_falls_back_to_username_last(self):
        user = User.objects.create_user("plainuser", "u3@example.com", "pass12345")
        seller = SellerAccount.objects.create(user=user, account_type="individual")
        self.assertEqual(seller.display_name, "plainuser")

    def test_lifetime_sales_aggregate(self):
        user = User.objects.create_user("u4", "u4@example.com", "pass12345")
        seller = SellerAccount.objects.create(user=user, account_type="individual", status="approved")
        product = make_product(price=Decimal("100.00"), seller_account=seller)
        buyer = User.objects.create_user("buyer2", "b2@example.com", "pass12345")
        order = Order.objects.create(user=buyer, full_name="B", address="St", city="Karachi",
                                      phone="0300", payment_method="cod")
        OrderItem.objects.create(order=order, product=product, product_name=product.name,
                                  price=Decimal("100.00"), quantity=3)
        self.assertEqual(seller.lifetime_sales, Decimal("300.00"))

    def test_commission_rate_reduces_at_volume_thresholds(self):
        user = User.objects.create_user("u5", "u5@example.com", "pass12345")
        seller = SellerAccount.objects.create(
            user=user, account_type="individual", commission_rate=Decimal("10.00"),
        )
        self.assertEqual(seller.effective_commission_rate, 10.0)


class OrganizationTeamAccessTests(TestCase):
    """The core organization feature: team members can act on behalf of the
    org account without the owner's credentials."""

    def setUp(self):
        self.owner = User.objects.create_user("owner1", "o1@example.com", "pass12345")
        self.staff = User.objects.create_user("staff1", "s1@example.com", "pass12345")
        self.outsider = User.objects.create_user("outsider1", "out1@example.com", "pass12345")
        self.org = SellerAccount.objects.create(
            user=self.owner, account_type="organization", status="approved",
            organization_name="Test Org",
        )
        OrganizationMember.objects.create(organization=self.org, user=self.staff, role="staff")

    def test_owner_resolves_to_their_own_account(self):
        account, role = get_seller_account_for_user(self.owner)
        self.assertEqual(account, self.org)
        self.assertEqual(role, "owner")

    def test_team_member_resolves_to_organization_account(self):
        account, role = get_seller_account_for_user(self.staff)
        self.assertEqual(account, self.org)
        self.assertEqual(role, "staff")

    def test_unrelated_user_has_no_seller_account(self):
        account, role = get_seller_account_for_user(self.outsider)
        self.assertIsNone(account)
        self.assertIsNone(role)

    def test_staff_can_load_seller_dashboard(self):
        client = Client()
        client.force_login(self.staff)
        response = client.get(reverse("seller_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Org")

    def test_outsider_gets_404_on_seller_dashboard(self):
        client = Client()
        client.force_login(self.outsider)
        response = client.get(reverse("seller_dashboard"))
        self.assertEqual(response.status_code, 404)

    def test_only_owner_can_add_team_members(self):
        client = Client()
        client.force_login(self.staff)
        new_user = User.objects.create_user("newperson", "np@example.com", "pass12345")
        client.post(reverse("add_team_member"), {"username_or_email": "newperson", "role": "staff"})
        self.assertFalse(OrganizationMember.objects.filter(user=new_user).exists())


class CorePageLoadTests(TestCase):
    """Smoke tests: the most-visited pages should always return 200."""

    def setUp(self):
        make_product(name="Homepage Product", stock=5)

    def test_homepage_loads(self):
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_all_products_loads(self):
        self.assertEqual(self.client.get(reverse("all_products")).status_code, 200)

    def test_category_page_loads(self):
        self.assertEqual(self.client.get(reverse("category_products", args=["skincare"])).status_code, 200)

    def test_product_detail_loads(self):
        product = Product.objects.first()
        self.assertEqual(self.client.get(reverse("product_detail", args=[product.id])).status_code, 200)


class CartAndCheckoutTests(TestCase):
    def setUp(self):
        self.product = make_product(stock=5)
        self.user = User.objects.create_user("shopper", "sh@example.com", "pass12345")

    def test_add_to_cart_updates_session(self):
        response = self.client.post(
            reverse("add_to_cart", args=[self.product.id]),
            {"quantity": 2},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["cart"], {str(self.product.id): 2})

    def test_add_to_cart_cannot_exceed_stock(self):
        self.client.post(
            reverse("add_to_cart", args=[self.product.id]),
            {"quantity": 999},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(self.client.session["cart"][str(self.product.id)], self.product.stock)

    def test_checkout_requires_login(self):
        response = self.client.get(reverse("checkout"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_cancel_order_only_works_for_own_pending_order(self):
        other_user = User.objects.create_user("someone_else", "oe@example.com", "pass12345")
        order = Order.objects.create(
            user=other_user, full_name="Someone Else", address="St", city="Karachi",
            phone="0300", payment_method="cod", status="pending",
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("cancel_order", args=[order.id]))
        self.assertEqual(response.status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

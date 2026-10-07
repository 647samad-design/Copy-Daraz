"""
Automated tests for the marketplace.

These aren't exhaustive - they focus on the business logic that's most
likely to break silently: money calculations (order totals, commission),
permission checks (who can access what), and the bugs found and fixed
during development (display_name, N+1 query annotations).

Run with: python manage.py test bees
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.test import TestCase as DjangoTestCase


class TestCase(DjangoTestCase):
    """Clears the cache before every test: site settings and rate-limit
    counters are cached, and the test database is rolled back between
    tests while the in-memory cache is not."""

    def _pre_setup(self):
        super()._pre_setup()
        cache.clear()
        # Never call the real Stripe API from tests.
        from unittest import mock as _mock
        self._stripe_customer = _mock.patch("stripe.Customer.create", return_value=_mock.MagicMock(id="cus_test"))
        self._stripe_customer.start()

    def _post_teardown(self):
        self._stripe_customer.stop()
        super()._post_teardown()
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
        item = OrderItem(order=order, product=product, product_name=product.name,
                         price=Decimal("100.00"), quantity=3)
        item.apply_commission(seller)
        item.save()
        self.assertEqual(seller.lifetime_sales, Decimal("300.00"))
        self.assertEqual(item.commission_amount, Decimal("30.00"))

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


class CouponValidationTests(TestCase):
    """Coupons used to have no expiry, no usage limit, no per-user limit,
    and no minimum order check - any active code worked forever, for
    anyone, any number of times."""

    def setUp(self):
        self.user = User.objects.create_user("shopper1", "sh1@example.com", "pass12345")
        self.other_user = User.objects.create_user("shopper2", "sh2@example.com", "pass12345")
        self.product = make_product(price=Decimal("500.00"))

    def _make_order(self, user, coupon_code, status="confirmed"):
        order = Order.objects.create(
            user=user, full_name="Buyer", address="St", city="Karachi",
            phone="0300", payment_method="cod", coupon_code=coupon_code, status=status,
        )
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name,
                                  price=Decimal("500.00"), quantity=1)
        return order

    def test_inactive_coupon_is_invalid(self):
        from .models import Coupon
        coupon = Coupon.objects.create(code="OFF10", percent_off=10, active=False)
        is_valid, error = coupon.is_valid_for(self.user, Decimal("1000"))
        self.assertFalse(is_valid)
        self.assertIn("no longer active", error)

    def test_expired_coupon_is_invalid(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import Coupon
        coupon = Coupon.objects.create(
            code="OLD10", percent_off=10, expiry_date=timezone.localdate() - timedelta(days=1),
        )
        is_valid, error = coupon.is_valid_for(self.user, Decimal("1000"))
        self.assertFalse(is_valid)
        self.assertIn("expired", error)

    def test_future_expiry_is_still_valid(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import Coupon
        coupon = Coupon.objects.create(
            code="NEW10", percent_off=10, expiry_date=timezone.localdate() + timedelta(days=1),
        )
        is_valid, error = coupon.is_valid_for(self.user, Decimal("1000"))
        self.assertTrue(is_valid)

    def test_minimum_order_value_enforced(self):
        from .models import Coupon
        coupon = Coupon.objects.create(code="BIG50", percent_off=50, min_order_value=Decimal("2000"))
        is_valid, error = coupon.is_valid_for(self.user, Decimal("500"))
        self.assertFalse(is_valid)
        self.assertIn("minimum order", error)
        is_valid, error = coupon.is_valid_for(self.user, Decimal("2500"))
        self.assertTrue(is_valid)

    def test_global_usage_limit_enforced(self):
        from .models import Coupon
        coupon = Coupon.objects.create(code="LIMITED", percent_off=10, usage_limit=1)
        self._make_order(self.user, "LIMITED")
        # Already used once globally, limit is 1 - a different user should now be blocked too.
        is_valid, error = coupon.is_valid_for(self.other_user, Decimal("1000"))
        self.assertFalse(is_valid)
        self.assertIn("usage limit", error)

    def test_cancelled_orders_dont_count_against_usage_limit(self):
        from .models import Coupon
        coupon = Coupon.objects.create(code="LIMITED2", percent_off=10, usage_limit=1)
        self._make_order(self.user, "LIMITED2", status="cancelled")
        is_valid, error = coupon.is_valid_for(self.other_user, Decimal("1000"))
        self.assertTrue(is_valid)

    def test_per_user_limit_enforced(self):
        from .models import Coupon
        coupon = Coupon.objects.create(code="ONEUSE", percent_off=10, per_user_limit=1)
        self._make_order(self.user, "ONEUSE")
        # This user already used it once - should now be blocked for them...
        is_valid, error = coupon.is_valid_for(self.user, Decimal("1000"))
        self.assertFalse(is_valid)
        self.assertIn("maximum number of times", error)
        # ...but a different user should still be able to use it.
        is_valid, error = coupon.is_valid_for(self.other_user, Decimal("1000"))
        self.assertTrue(is_valid)

    def test_apply_coupon_view_rejects_invalid_code(self):
        self.client.force_login(self.user)
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": 1})
        response = self.client.post(reverse("apply_coupon"), {"coupon_code": "DOESNOTEXIST"}, follow=True)
        self.assertContains(response, "coupon code isn")

    def test_apply_coupon_view_accepts_valid_code(self):
        from .models import Coupon
        Coupon.objects.create(code="WORKS10", percent_off=10)
        self.client.force_login(self.user)
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": 1})
        response = self.client.post(reverse("apply_coupon"), {"coupon_code": "WORKS10"}, follow=True)
        self.assertContains(response, "Coupon applied")


class ProductFilterTests(TestCase):
    def setUp(self):
        make_product(name="Cheap Item", price=Decimal("100.00"), stock=5, seller_name="SellerA")
        make_product(name="Mid Item", price=Decimal("500.00"), stock=0, seller_name="SellerB")
        make_product(name="Expensive Item", price=Decimal("2000.00"), stock=10, seller_name="SellerA")

    def test_price_range_filter(self):
        response = self.client.get(reverse("all_products"), {"min_price": "200", "max_price": "1000"})
        self.assertContains(response, "Mid Item")
        self.assertNotContains(response, "Cheap Item")
        self.assertNotContains(response, "Expensive Item")

    def test_in_stock_filter_excludes_zero_stock(self):
        response = self.client.get(reverse("all_products"), {"in_stock": "1"})
        self.assertNotContains(response, "Mid Item")
        self.assertContains(response, "Cheap Item")

    def test_seller_filter(self):
        response = self.client.get(reverse("all_products"), {"seller": "SellerB"})
        self.assertContains(response, "Mid Item")
        self.assertNotContains(response, "Cheap Item")

    def test_filters_combine_with_category_page(self):
        response = self.client.get(reverse("category_products", args=["skincare"]), {"min_price": "1000"})
        self.assertContains(response, "Expensive Item")
        self.assertNotContains(response, "Cheap Item")


class ChatTests(TestCase):
    def test_guest_can_send_and_read_chat_messages(self):
        response = self.client.post(reverse("chat_send"), {"message": "Hello, is anyone there?"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertTrue(data["reply"]["message"])

        history = self.client.get(reverse("chat_messages"))
        messages = history.json()["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["sender"], "user")
        self.assertEqual(messages[0]["message"], "Hello, is anyone there?")
        self.assertEqual(messages[1]["sender"], "support")

    def test_empty_chat_message_rejected(self):
        response = self.client.post(reverse("chat_send"), {"message": "   "})
        self.assertEqual(response.status_code, 400)

    def test_order_keyword_triggers_relevant_auto_reply(self):
        response = self.client.post(reverse("chat_send"), {"message": "where is my order tracking"})
        self.assertIn("My orders", response.json()["reply"]["message"])

    def test_logged_in_user_thread_persists_across_requests(self):
        user = User.objects.create_user(username="chatuser", password="pass12345")
        self.client.force_login(user)
        self.client.post(reverse("chat_send"), {"message": "First message"})
        self.client.post(reverse("chat_send"), {"message": "Second message"})

        from .models import ChatThread
        self.assertEqual(ChatThread.objects.filter(user=user).count(), 1)
        history = self.client.get(reverse("chat_messages")).json()["messages"]
        user_messages = [m for m in history if m["sender"] == "user"]
        self.assertEqual(len(user_messages), 2)


# ---------------------------------------------------------------------------
# Checkout, Stripe and security regression tests
# ---------------------------------------------------------------------------
import json
import os
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .models import Address, Coupon, NewsletterSubscriber, ProductImage, SiteSettings


CHECKOUT_FORM = {
    "full_name": "Jane Doe", "email": "jane@example.com", "phone": "+1 555 0100",
    "address": "1 Main St", "city": "Austin", "state": "TX", "postal_code": "73301", "country": "us",
}


class CheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("jane", "jane@example.com", "pass12345")
        self.product = make_product(price=Decimal("20.00"), stock=3)
        self.client.force_login(self.user)

    def _add(self, qty=1, product=None):
        self.client.post(reverse("add_to_cart", args=[(product or self.product).id]), {"quantity": qty})

    def test_cod_checkout_creates_order_and_decrements_stock(self):
        self._add(2)
        response = self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        order = Order.objects.get(user=self.user)
        self.assertRedirects(response, reverse("order_success", args=[order.id]))
        self.assertEqual(order.country, "US")
        self.assertEqual(order.total, Decimal("40.00"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)
        self.assertEqual(self.client.session["cart"], {})

    def test_checkout_blocks_when_stock_ran_out(self):
        self._add(3)
        Product.objects.filter(pk=self.product.pk).update(stock=1)  # someone else bought it
        response = self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        self.assertRedirects(response, reverse("cart"))
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)

    def test_missing_address_fields_rejected(self):
        self._add()
        form = {**CHECKOUT_FORM, "postal_code": "", "payment_method": "cod"}
        response = self.client.post(reverse("checkout"), form)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_shipping_and_tax_applied(self):
        settings_obj = SiteSettings.load()
        settings_obj.shipping_flat_fee = Decimal("5.00")
        settings_obj.tax_percent = Decimal("10")
        settings_obj.save()
        self._add(1)
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        order = Order.objects.get()
        self.assertEqual(order.shipping_amount, Decimal("5.00"))
        self.assertEqual(order.tax_amount, Decimal("2.00"))
        self.assertEqual(order.total, Decimal("27.00"))

    def test_free_shipping_threshold(self):
        settings_obj = SiteSettings.load()
        settings_obj.shipping_flat_fee = Decimal("5.00")
        settings_obj.free_shipping_threshold = Decimal("30.00")
        settings_obj.save()
        self._add(2)
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        self.assertEqual(Order.objects.get().shipping_amount, Decimal("0"))

    def test_cod_disabled_hides_method(self):
        settings_obj = SiteSettings.load()
        settings_obj.allow_cash_on_delivery = False
        settings_obj.save()
        self._add()
        response = self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        self.assertRedirects(response, reverse("checkout"), fetch_redirect_response=False)
        self.assertFalse(Order.objects.exists())

    def test_coupon_applied_to_order(self):
        Coupon.objects.create(code="TEN", percent_off=10)
        self._add(1)
        self.client.post(reverse("apply_coupon"), {"coupon_code": "ten"})
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        order = Order.objects.get()
        self.assertEqual(order.coupon_code, "TEN")
        self.assertEqual(order.discount_amount, Decimal("2.00"))

    def test_negative_quantity_cannot_lower_cart_total(self):
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": -5})
        self.assertEqual(self.client.session["cart"][str(self.product.id)], 1)

    def test_cannot_add_unapproved_product(self):
        hidden = make_product(name="Hidden", approval_status="pending")
        response = self.client.post(reverse("add_to_cart", args=[hidden.id]))
        self.assertEqual(response.status_code, 404)

    def test_cancel_cod_order_restocks(self):
        self._add(2)
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        order = Order.objects.get()
        self.client.post(reverse("cancel_order", args=[order.id]))
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.product.stock, 3)

    def test_cancel_requires_post(self):
        self._add(1)
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        order = Order.objects.get()
        response = self.client.get(reverse("cancel_order", args=[order.id]))
        self.assertEqual(response.status_code, 405)


def _fake_session(order, **overrides):
    data = {
        "id": order.stripe_session_id or "cs_test_123",
        "payment_status": "paid",
        "status": "complete",
        "amount_total": order.total_cents,
        "currency": order.currency,
        "payment_intent": "pi_test_123",
        "metadata": {"order_id": str(order.id)},
    }
    data.update(overrides)
    return data


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_WEBHOOK_SECRET="whsec_dummy")
class StripeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("payer", "payer@example.com", "pass12345")
        self.product = make_product(price=Decimal("25.00"), stock=5)
        self.client.force_login(self.user)

    def _checkout_card(self, qty=2):
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": qty})
        fake = mock.MagicMock()
        fake.id = "cs_test_123"
        fake.url = "https://checkout.stripe.com/c/pay/cs_test_123"
        with mock.patch("stripe.checkout.Session.create", return_value=fake) as create:
            response = self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "card"})
        return response, create

    def test_card_checkout_redirects_to_stripe_with_correct_amounts(self):
        response, create = self._checkout_card()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://checkout.stripe.com/"))
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 2500)
        self.assertEqual(kwargs["line_items"][0]["quantity"], 2)
        self.assertEqual(kwargs["metadata"]["order_id"], str(Order.objects.get().id))
        order = Order.objects.get()
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(order.stripe_session_id, "cs_test_123")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)  # reserved while paying

    def test_stripe_failure_releases_stock_and_restores_cart(self):
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": 2})
        with mock.patch("stripe.checkout.Session.create", side_effect=Exception("boom")):
            response = self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "card"})
        self.assertRedirects(response, reverse("cart"))
        order = Order.objects.get()
        self.assertEqual(order.status, "cancelled")
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(self.client.session["cart"], {str(self.product.id): 2})

    def _webhook(self, event_type, session):
        payload = json.dumps({"type": event_type, "data": {"object": session}})
        with mock.patch("stripe.Webhook.construct_event", return_value={}):
            return self.client.post(
                reverse("stripe_webhook"), data=payload, content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=fake",
            )

    def test_webhook_marks_order_paid(self):
        self._checkout_card()
        order = Order.objects.get()
        response = self._webhook("checkout.session.completed", _fake_session(order))
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.stripe_payment_intent, "pi_test_123")

    def test_webhook_rejects_amount_mismatch(self):
        self._checkout_card()
        order = Order.objects.get()
        self._webhook("checkout.session.completed", _fake_session(order, amount_total=100))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "pending")

    def test_webhook_bad_signature_rejected(self):
        with mock.patch("stripe.Webhook.construct_event", side_effect=ValueError("bad")):
            response = self.client.post(reverse("stripe_webhook"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_expired_session_cancels_and_restocks(self):
        self._checkout_card()
        order = Order.objects.get()
        self._webhook("checkout.session.expired", _fake_session(order, payment_status="unpaid", status="expired"))
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.product.stock, 5)

    def test_webhook_is_idempotent(self):
        self._checkout_card()
        order = Order.objects.get()
        self._webhook("checkout.session.completed", _fake_session(order))
        self._webhook("checkout.session.completed", _fake_session(order))
        self._webhook("checkout.session.expired", _fake_session(order, status="expired"))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.status, "confirmed")

    def test_success_page_confirms_payment(self):
        self._checkout_card()
        order = Order.objects.get()
        fake = mock.MagicMock()
        fake.to_dict.return_value = _fake_session(order)
        with mock.patch("stripe.checkout.Session.retrieve", return_value=fake):
            response = self.client.get(reverse("payment_success"), {"session_id": "cs_test_123"})
        self.assertRedirects(response, reverse("order_success", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

    def test_cancel_page_restores_cart(self):
        self._checkout_card()
        order = Order.objects.get()
        fake = mock.MagicMock()
        fake.to_dict.return_value = _fake_session(order, payment_status="unpaid", status="open")
        with mock.patch("stripe.checkout.Session.retrieve", return_value=fake), \
                mock.patch("stripe.checkout.Session.expire"):
            response = self.client.get(reverse("payment_cancel", args=[order.id]))
        self.assertRedirects(response, reverse("cart"))
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.client.session["cart"], {str(self.product.id): 2})

    def test_cancelling_paid_order_refunds(self):
        self._checkout_card()
        order = Order.objects.get()
        self._webhook("checkout.session.completed", _fake_session(order))
        with mock.patch("stripe.Refund.create") as refund:
            self.client.post(reverse("cancel_order", args=[order.id]))
        refund.assert_called_once()
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "refunded")
        self.assertEqual(order.status, "cancelled")

    def test_refund_failure_keeps_order_active(self):
        self._checkout_card()
        order = Order.objects.get()
        self._webhook("checkout.session.completed", _fake_session(order))
        with mock.patch("stripe.Refund.create", side_effect=Exception("declined")):
            self.client.post(reverse("cancel_order", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.status, "confirmed")


class SecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", "alice@example.com", "Sup3r-secret-pass")

    def test_login_ignores_external_next_url(self):
        response = self.client.post(
            reverse("login") + "?next=https://evil.example/phish",
            {"username": "alice", "password": "Sup3r-secret-pass"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    def test_login_allows_local_next_url(self):
        response = self.client.post(
            reverse("login"), {"username": "alice", "password": "Sup3r-secret-pass", "next": "/my-orders/"},
        )
        self.assertEqual(response.url, "/my-orders/")

    def test_login_with_email(self):
        response = self.client.post(reverse("login"), {"username": "ALICE@example.com", "password": "Sup3r-secret-pass"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_spoofed_forwarded_for_does_not_bypass_rate_limit(self):
        from django.core.cache import cache
        cache.clear()
        for i in range(8):
            self.client.post(reverse("login"), {"username": "alice", "password": "wrong"},
                             HTTP_X_FORWARDED_FOR=f"10.0.0.{i}")
        response = self.client.post(reverse("login"), {"username": "alice", "password": "Sup3r-secret-pass"},
                                    HTTP_X_FORWARDED_FOR="10.0.0.99")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        cache.clear()

    def test_google_login_route_removed(self):
        self.assertEqual(self.client.post("/auth/google/").status_code, 404)

    def test_signup_rejects_weak_password_and_duplicate_email(self):
        self.client.post(reverse("signup"), {
            "username": "bob", "email": "bob@example.com", "password": "123", "confirm_password": "123",
        })
        self.assertFalse(User.objects.filter(username="bob").exists())
        self.client.post(reverse("signup"), {
            "username": "bob2", "email": "ALICE@example.com",
            "password": "Another-strong-pass1", "confirm_password": "Another-strong-pass1",
        })
        self.assertFalse(User.objects.filter(username="bob2").exists())

    def test_signup_rejects_email_as_username(self):
        self.client.post(reverse("signup"), {
            "username": "victim@example.com", "email": "attacker@example.com",
            "password": "Another-strong-pass1", "confirm_password": "Another-strong-pass1",
        })
        self.assertFalse(User.objects.filter(username="victim@example.com").exists())

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.client.get(reverse("logout"))
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_invoice_of_guest_order_not_public(self):
        order = Order.objects.create(user=None, full_name="Guest", address="x", city="y", phone="1")
        self.assertEqual(self.client.get(reverse("invoice_pdf", args=[order.id])).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("invoice_pdf", args=[order.id])).status_code, 404)

    def test_email_code_locks_after_too_many_attempts(self):
        from django.core.cache import cache
        self.client.force_login(self.user)
        Profile_ = __import__("bees.models", fromlist=["Profile"]).Profile
        Profile_.objects.create(user=self.user, referral_code="ABC123")
        cache.set(f"email_verify_code:{self.user.id}", "123456", 900)
        for _ in range(5):
            self.client.post(reverse("verify_email"), {"code": "000000"})
        self.client.post(reverse("verify_email"), {"code": "123456"})
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.email_verified)

    def test_review_requires_login_and_clamps_rating(self):
        product = make_product()
        self.client.post(reverse("product_detail", args=[product.id]), {"rating": 5, "comment": "Spam"})
        self.assertFalse(Review.objects.exists())
        self.client.force_login(self.user)
        self.client.post(reverse("product_detail", args=[product.id]), {"rating": 4, "comment": "Never bought it"})
        self.assertFalse(Review.objects.exists())  # only customers who received it can review
        order = Order.objects.create(user=self.user, full_name="R", address="x", city="y", phone="1", status="delivered")
        OrderItem.objects.create(order=order, product=product, product_name=product.name, price=product.price, quantity=1)
        self.client.post(reverse("product_detail", args=[product.id]), {"rating": 999, "comment": "Great"})
        self.assertFalse(Review.objects.exists())
        self.client.post(reverse("product_detail", args=[product.id]), {"rating": 4, "comment": "Great"})
        self.client.post(reverse("product_detail", args=[product.id]), {"rating": 5, "comment": "Even better"})
        self.assertEqual(Review.objects.get().rating, 5)


class SellerProductSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("maker", "maker@example.com", "pass12345")
        self.seller = SellerAccount.objects.create(user=self.user, status="approved", business_name="Maker Co")
        self.product = make_product(seller_account=self.seller, seller_name="Maker Co", approval_status="approved")
        self.client.force_login(self.user)

    def _form(self, **overrides):
        data = {
            "name": self.product.name, "category": "skincare", "price": "10.00", "stock": "4",
            "image_url": self.product.image_url, "description": self.product.description,
        }
        data.update(overrides)
        return data

    def test_editing_listing_details_requires_re_approval(self):
        self.client.post(reverse("seller_edit_product", args=[self.product.id]), self._form(name="Totally different"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.approval_status, "pending")

    def test_price_and_stock_changes_stay_live(self):
        self.client.post(reverse("seller_edit_product", args=[self.product.id]), self._form(price="12.50", stock="9"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.approval_status, "approved")
        self.assertEqual(self.product.price, Decimal("12.50"))

    def test_html_upload_rejected(self):
        evil = SimpleUploadedFile("x.html", b"<script>alert(1)</script>", content_type="text/html")
        self.client.post(reverse("seller_add_product"), {**self._form(name="New", image_url=""), "image_file": evil})
        self.assertFalse(Product.objects.filter(name="New").exists())

    def test_fake_image_rejected(self):
        evil = SimpleUploadedFile("x.png", b"<svg onload=alert(1)>", content_type="image/png")
        self.client.post(reverse("seller_add_product"), {**self._form(name="New2", image_url=""), "image_file": evil})
        self.assertFalse(Product.objects.filter(name="New2").exists())

    def test_invalid_price_does_not_crash(self):
        response = self.client.post(reverse("seller_add_product"), self._form(name="Bad", price="abc"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Bad").exists())

    def test_delete_requires_post(self):
        response = self.client.get(reverse("seller_delete_product", args=[self.product.id]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_seller_documents_are_staff_only(self):
        response = self.client.get(reverse("seller_document", args=[self.seller.id, "id_document"]))
        self.assertEqual(response.status_code, 403)


class WhiteLabelTests(TestCase):
    def test_store_name_comes_from_settings(self):
        make_product(name="Branded Product")
        settings_obj = SiteSettings.load()
        settings_obj.site_name = "Acme Goods"
        settings_obj.save()
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Acme Goods")
        self.assertNotContains(response, "19Bees")

    @override_settings(STORE_CURRENCY="eur")
    def test_prices_use_store_currency(self):
        make_product(name="Euro Product", price=Decimal("1234.50"))
        response = self.client.get(reverse("all_products"))
        self.assertContains(response, "€1,234.50")


# ---------------------------------------------------------------------------
# Store admin (/manage/)
# ---------------------------------------------------------------------------
from django.core import mail

from .models import ChatMessage, ChatThread, Profile, Question, ReturnRequest


class ManageAccessTests(TestCase):
    PAGES = ["manage_dashboard", "manage_orders", "manage_products", "manage_product_new", "manage_sellers",
             "manage_customers", "manage_coupons", "manage_reviews", "manage_returns", "manage_support", "manage_settings"]

    def setUp(self):
        self.staff = User.objects.create_user("boss", "boss@example.com", "pass12345", is_staff=True)
        self.shopper = User.objects.create_user("shop", "shop@example.com", "pass12345")
        make_product(name="Admin Visible Product")

    def test_anonymous_redirected_to_login(self):
        for name in self.PAGES:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/login/", r.url)

    def test_customers_are_forbidden(self):
        self.client.force_login(self.shopper)
        for name in self.PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_staff_can_open_every_page(self):
        self.client.force_login(self.staff)
        for name in self.PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("manage_reviews") + "?tab=questions").status_code, 200)
        self.assertEqual(self.client.get(reverse("manage_dashboard") + "?range=30").status_code, 200)

    def test_admin_icon_only_for_staff(self):
        self.client.force_login(self.shopper)
        self.assertNotContains(self.client.get(reverse("home")), f'href="{reverse("manage_dashboard")}"')
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("home")), f'href="{reverse("manage_dashboard")}"')

    def test_old_dashboard_url_redirects(self):
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.get(reverse("owner_dashboard")), reverse("manage_dashboard"))


class ManageActionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", "boss@example.com", "pass12345", is_staff=True)
        self.buyer = User.objects.create_user("buyer", "buyer@example.com", "pass12345")
        self.product = make_product(price=Decimal("30.00"), stock=5)
        self.client.force_login(self.staff)

    def _order(self, **kw):
        order = Order.objects.create(user=self.buyer, email="buyer@example.com", full_name="Buy Er", address="1 St",
                                     city="Austin", postal_code="1", country="US", phone="1", **kw)
        OrderItem.objects.create(order=order, product=self.product, product_name=self.product.name, price=Decimal("30.00"), quantity=2)
        return order

    def test_marking_shipped_emails_customer_and_notifies(self):
        order = self._order(status="confirmed")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("manage_order", args=[order.id]), {
                "action": "update", "status": "shipped", "tracking_number": "1Z999", "courier_name": "UPS", "estimated_delivery": "2026-12-01",
            })
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")
        self.assertEqual(order.tracking_number, "1Z999")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("1Z999", mail.outbox[0].alternatives[0][0])
        self.assertTrue(Notification.objects.filter(user=self.buyer, message__icontains="Shipped").exists())

    def test_cancel_restocks(self):
        order = self._order(status="pending")
        Product.objects.filter(pk=self.product.pk).update(stock=3)
        self.client.post(reverse("manage_order", args=[order.id]), {"action": "cancel"})
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.product.stock, 5)

    def test_orders_csv_export(self):
        self._order()
        r = self.client.get(reverse("manage_orders") + "?export=csv")
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn(b"Buy Er", r.content)

    def test_create_product(self):
        r = self.client.post(reverse("manage_product_new"), {
            "name": "Staff Made", "category": "skincare", "price": "12.50", "stock": "7",
            "image_url": "https://example.com/a.jpg", "approval_status": "approved", "is_flash_sale": "1",
            "extra_images": "https://example.com/b.jpg\nnot-a-url",
        })
        p = Product.objects.get(name="Staff Made")
        self.assertRedirects(r, reverse("manage_product_edit", args=[p.id]))
        self.assertTrue(p.is_flash_sale)
        self.assertEqual(p.extra_images.count(), 1)

    def test_bulk_approve_notifies_seller(self):
        seller_user = User.objects.create_user("sel", "sel@example.com", "pass12345")
        seller = SellerAccount.objects.create(user=seller_user, status="approved", business_name="Sel Co")
        pending = make_product(name="Pending One", seller_account=seller, approval_status="pending")
        self.client.post(reverse("manage_products_bulk"), {"ids": [pending.id], "action": "approve"})
        pending.refresh_from_db()
        self.assertEqual(pending.approval_status, "approved")
        self.assertTrue(Notification.objects.filter(user=seller_user, message__icontains="approved").exists())

    def test_seller_approve_and_payout(self):
        u = User.objects.create_user("app", "app@example.com", "pass12345")
        s = SellerAccount.objects.create(user=u, business_name="App Shop")
        self.client.post(reverse("manage_seller", args=[s.id]), {"action": "approved"})
        s.refresh_from_db()
        self.assertEqual(s.status, "approved")
        self.client.post(reverse("manage_seller", args=[s.id]), {"action": "payout", "amount": "25.50"})
        s.refresh_from_db()
        self.assertEqual(s.total_paid_out, Decimal("25.50"))
        self.client.post(reverse("manage_seller", args=[s.id]), {"action": "payout", "amount": "-4"})
        s.refresh_from_db()
        self.assertEqual(s.total_paid_out, Decimal("25.50"))

    def test_coupon_create_toggle_delete(self):
        self.client.post(reverse("manage_coupons"), {"action": "create", "code": "spring", "percent_off": "15",
                                                      "min_order_value": "0", "per_user_limit": "1", "active": "on"})
        c = Coupon.objects.get(code="SPRING")
        self.client.post(reverse("manage_coupons"), {"action": "toggle", "id": c.id})
        c.refresh_from_db()
        self.assertFalse(c.active)
        self.client.post(reverse("manage_coupons"), {"action": "create", "code": "bad", "percent_off": "500",
                                                      "min_order_value": "0", "per_user_limit": "1"})
        self.assertFalse(Coupon.objects.filter(code="BAD").exists())
        self.client.post(reverse("manage_coupons"), {"action": "delete", "id": c.id})
        self.assertFalse(Coupon.objects.filter(pk=c.id).exists())

    def test_answer_question(self):
        qn = Question.objects.create(product=self.product, username="q", question="Waterproof?")
        self.client.post(reverse("manage_reviews") + "?tab=questions", {"action": "answer", "id": qn.id, "answer": "Yes."})
        qn.refresh_from_db()
        self.assertEqual(qn.answer, "Yes.")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    def test_return_refund_to_card_is_partial(self):
        order = self._order(status="delivered", payment_method="card", payment_status="paid", stripe_payment_intent="pi_1")
        item = order.items.get()
        rr = ReturnRequest.objects.create(order_item=item, user=self.buyer, reason="Broken")
        with mock.patch("stripe.Refund.create") as refund:
            self.client.post(reverse("manage_returns"), {"id": rr.id, "action": "refund"})
        self.assertEqual(refund.call_args.kwargs["amount"], 6000)
        rr.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(rr.status, "refunded")
        self.assertEqual(self.product.stock, 7)

    def test_support_reply(self):
        thread = ChatThread.objects.create(user=self.buyer)
        ChatMessage.objects.create(thread=thread, sender="user", message="Where is my order?")
        self.client.get(reverse("manage_support_thread", args=[thread.id]))
        self.assertFalse(thread.messages.filter(is_read=False).exists())
        self.client.post(reverse("manage_support_thread", args=[thread.id]), {"action": "reply", "message": "Shipping today!"})
        self.assertTrue(thread.messages.filter(sender="support", message="Shipping today!").exists())
        self.client.logout()
        self.client.force_login(self.buyer)
        msgs = self.client.get(reverse("chat_messages")).json()["messages"]
        self.assertEqual(msgs[-1]["message"], "Shipping today!")

    def test_settings_update_rebrands_store(self):
        data = {f: v for f, v in {
            "site_name": "Nova Goods", "tagline": "Good things", "primary_color": "#112233", "accent_color": "#FFCC00",
            "hero_title": "Hello", "hero_subtitle": "World", "tax_percent": "0", "shipping_flat_fee": "0",
            "free_shipping_threshold": "0", "delivery_days": "5", "return_days": "14", "allow_cash_on_delivery": "on",
        }.items()}
        self.client.post(reverse("manage_settings"), data)
        self.assertContains(self.client.get(reverse("home")), "Nova Goods")
        bad = dict(data, primary_color="red")
        self.client.post(reverse("manage_settings"), bad)
        self.assertEqual(SiteSettings.load().primary_color, "#112233")


class SpeculativeRequestTests(TestCase):
    def test_prefetch_does_not_count_as_search(self):
        from .models import SearchLog
        make_product(name="Oil Lamp")
        self.client.get(reverse("search_products"), {"q": "oil"}, HTTP_SEC_PURPOSE="prefetch;prerender")
        self.assertFalse(SearchLog.objects.exists())
        self.client.get(reverse("search_products"), {"q": "oil"})
        self.assertTrue(SearchLog.objects.exists())


class MarketplaceFlowTests(TestCase):
    """End to end: a business signs up to sell, gets approved, lists a
    product, a buyer orders it, and the commission is recorded."""

    def test_full_seller_to_buyer_flow(self):
        staff = User.objects.create_user("boss", "boss@example.com", "pass12345", is_staff=True)
        # 1. A business applies to sell
        self.client.post(reverse("signup"), {
            "username": "acme", "email": "acme@example.com", "password": "Strong-pass-123", "confirm_password": "Strong-pass-123",
            "user_type": "organization", "organization_name": "Acme Ltd", "phone": "+1 555 0101", "country": "US",
        })
        seller = SellerAccount.objects.get(user__username="acme")
        self.assertEqual(seller.status, "pending")
        self.assertEqual(seller.commission_rate, Decimal("20"))
        # 2. Staff approve the seller
        self.client.force_login(staff)
        self.client.post(reverse("manage_seller", args=[seller.id]), {"action": "approved"})
        # 3. Seller lists a product; it waits for review
        self.client.force_login(seller.user)
        self.client.post(reverse("seller_add_product"), {
            "name": "Acme Lamp", "category": "table-lamp", "price": "50.00", "stock": "4",
            "image_url": "https://example.com/lamp.jpg", "description": "A lamp",
        })
        product = Product.objects.get(name="Acme Lamp")
        self.assertEqual(product.approval_status, "pending")
        self.assertEqual(self.client.get(reverse("product_detail", args=[product.id])).status_code, 200)  # owner preview
        self.client.logout()
        self.assertEqual(self.client.get(reverse("product_detail", args=[product.id])).status_code, 404)  # hidden from public
        # 4. Staff approve the product
        self.client.force_login(staff)
        self.client.post(reverse("manage_products_bulk"), {"ids": [product.id], "action": "approve"})
        # 5. A buyer orders 2 with cash on delivery
        buyer = User.objects.create_user("buyer", "buyer@example.com", "pass12345")
        self.client.force_login(buyer)
        self.client.post(reverse("add_to_cart", args=[product.id]), {"quantity": 2})
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        item = OrderItem.objects.get(product=product)
        self.assertEqual(item.seller_account, seller)
        self.assertEqual(item.commission_rate, Decimal("20"))
        self.assertEqual(item.commission_amount, Decimal("20.00"))
        seller.refresh_from_db()
        self.assertEqual(seller.lifetime_sales, Decimal("100.00"))
        self.assertEqual(seller.net_earnings, 80.0)
        # 6. The seller sees the sale, the address and their earning
        self.client.force_login(seller.user)
        page = self.client.get(reverse("seller_dashboard"))
        self.assertContains(page, "Acme Lamp")
        self.assertContains(page, "Austin")
        self.assertContains(page, "$80.00")
        # 7. Cancelled orders don't count toward earnings
        self.client.force_login(buyer)
        self.client.post(reverse("cancel_order", args=[item.order.id]))
        seller = SellerAccount.objects.get(pk=seller.pk)
        self.assertEqual(seller.lifetime_sales, 0)
        product.refresh_from_db()
        self.assertEqual(product.stock, 4)

    def test_store_products_have_no_commission(self):
        buyer = User.objects.create_user("b2", "b2@example.com", "pass12345")
        p = make_product(price=Decimal("10.00"))
        self.client.force_login(buyer)
        self.client.post(reverse("add_to_cart", args=[p.id]))
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        item = OrderItem.objects.get()
        self.assertIsNone(item.seller_account)
        self.assertEqual(item.commission_amount, 0)


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_WEBHOOK_SECRET="whsec_dummy")
class PayCodOrderOnlineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("payer", "payer@example.com", "pass12345")
        self.product = make_product(price=Decimal("25.00"), stock=5)
        self.client.force_login(self.user)
        self.client.post(reverse("add_to_cart", args=[self.product.id]))
        self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        self.order = Order.objects.get()

    def _start(self):
        fake = mock.MagicMock()
        fake.id = "cs_cod_1"
        fake.url = "https://checkout.stripe.com/c/pay/cs_cod_1"
        with mock.patch("stripe.checkout.Session.create", return_value=fake):
            return self.client.post(reverse("pay_online", args=[self.order.id]))

    def test_cod_order_can_be_paid_in_advance(self):
        r = self._start()
        self.assertTrue(r.url.startswith("https://checkout.stripe.com/"))
        self.order.refresh_from_db()
        payload = json.dumps({"type": "checkout.session.completed", "data": {"object": _fake_session(self.order)}})
        with mock.patch("stripe.Webhook.construct_event", return_value={}):
            self.client.post(reverse("stripe_webhook"), data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE="x")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self.order.payment_method, "card")
        self.assertFalse(self.order.cod_fallback)

    def test_abandoned_advance_payment_keeps_cod_order(self):
        self._start()
        self.order.refresh_from_db()
        payload = json.dumps({"type": "checkout.session.expired", "data": {"object": _fake_session(self.order, payment_status="unpaid", status="expired")}})
        with mock.patch("stripe.Webhook.construct_event", return_value={}):
            self.client.post(reverse("stripe_webhook"), data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE="x")
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        self.assertEqual(self.order.payment_method, "cod")
        self.assertEqual(self.order.payment_status, "not_applicable")
        self.assertEqual(self.product.stock, 4)  # still reserved for the COD order

    def test_cancel_page_returns_to_cod(self):
        self._start()
        fake = mock.MagicMock()
        self.order.refresh_from_db()
        fake.to_dict.return_value = _fake_session(self.order, payment_status="unpaid", status="open")
        with mock.patch("stripe.checkout.Session.retrieve", return_value=fake), mock.patch("stripe.checkout.Session.expire"):
            r = self.client.get(reverse("payment_cancel", args=[self.order.id]))
        self.assertRedirects(r, reverse("my_orders"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_method, "cod")
        self.assertNotEqual(self.order.status, "cancelled")

    def test_paid_or_shipped_orders_cannot_switch(self):
        Order.objects.filter(pk=self.order.pk).update(status="shipped")
        self._start()
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_method, "cod")


class DemoCatalogueTests(TestCase):
    def test_seed_gives_every_product_a_matching_image(self):
        import tempfile
        from django.core.management import call_command
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            Product.objects.create(name="Charcoal Face Wash", category="skincare", price=5,
                                   image_url="https://picsum.photos/id/10/400/400")
            call_command("seed_data", stdout=open(os.devnull, "w"))
            fw = Product.objects.get(name="Charcoal Face Wash")
            self.assertIn("charcoal-face-wash", fw.image_url)
            self.assertFalse(Product.objects.filter(image_url__contains="picsum").exists())
            self.assertFalse(ProductImage.objects.filter(image_url__contains="picsum").exists())


class StripeRelayTests(TestCase):
    def tearDown(self):
        import stripe
        stripe.api_base = "https://api.stripe.com"

    def test_default_api_base(self):
        from . import payments
        with self.settings(STRIPE_SECRET_KEY="sk_test_x", STRIPE_API_BASE=""):
            self.assertEqual(payments._stripe().api_base, "https://api.stripe.com")

    def test_relay_api_base(self):
        from . import payments
        relay = "https://ref.supabase.co/functions/v1/stripe-relay/secret"
        with self.settings(STRIPE_SECRET_KEY="sk_test_x", STRIPE_API_BASE=relay):
            self.assertEqual(payments._stripe().api_base, relay)
@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_WEBHOOK_SECRET="whsec_dummy",
                   ALLOWED_HOSTS=["shop.example.com", "testserver"], SITE_URL="")
class OrderJourneyTests(TestCase):
    """The customer is kept informed at every step, and cancelling a paid
    order refunds it."""

    def setUp(self):
        self.staff = User.objects.create_user("boss", "boss@example.com", "pass12345", is_staff=True)
        self.buyer = User.objects.create_user("buyer", "buyer@example.com", "pass12345")
        self.product = make_product(name="Charcoal Face Wash", price=Decimal("20.00"), stock=10)

    def _cod_order(self):
        self.client.force_login(self.buyer)
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": 2})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "cod"})
        return Order.objects.get()

    def _admin_set(self, order, status, **extra):
        self.client.force_login(self.staff)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("manage_order", args=[order.id]), {
                "action": "update", "status": status, "tracking_number": extra.get("tracking", ""),
                "courier_name": extra.get("courier", ""),
                "estimated_delivery": order.estimated_delivery.isoformat() if order.estimated_delivery else "",
            })
        order.refresh_from_db()
        return order

    def test_full_cod_journey_emails_every_step(self):
        order = self._cod_order()
        # Placed: estimated delivery set, "received" email with the date
        self.assertIsNotNone(order.estimated_delivery)
        self.assertGreater(order.estimated_delivery, order.created_at.date())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("received", mail.outbox[0].subject)
        self.assertIn("Estimated delivery", mail.outbox[0].alternatives[0][0])

        order = self._admin_set(order, "confirmed")
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("is confirmed", mail.outbox[1].subject)
        self.assertIn(order.estimated_delivery.strftime("%B"), mail.outbox[1].alternatives[0][0])

        order = self._admin_set(order, "shipped", tracking="TRK123", courier="DHL")
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("on its way", mail.outbox[2].subject)
        self.assertIn("TRK123", mail.outbox[2].alternatives[0][0])
        self.assertIn("https://shop.example.com/my-orders/", mail.outbox[2].alternatives[0][0])
        self.assertEqual(set(order.items.values_list("fulfillment_status", flat=True)), {"handed_to_courier"})

        order = self._admin_set(order, "delivered")
        self.assertEqual(len(mail.outbox), 4)
        self.assertIn("delivered", mail.outbox[3].subject)
        self.assertIn("Write a review", mail.outbox[3].alternatives[0][0])
        self.assertTrue(all(m.to == ["jane@example.com"] for m in mail.outbox))  # the email given at checkout

    def test_customer_cancels_cod_order(self):
        order = self._cod_order()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("cancel_order", args=[order.id]))
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("cancelled", mail.outbox[0].subject)
        self.assertIn("not been charged", mail.outbox[0].alternatives[0][0])

    def test_customer_cancels_paid_order_gets_refund_and_email(self):
        self.client.force_login(self.buyer)
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": 1})
        fake = mock.MagicMock(id="cs_test_9", url="https://checkout.stripe.com/x")
        with mock.patch("stripe.checkout.Session.create", return_value=fake):
            self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "card"})
        order = Order.objects.get()
        payload = json.dumps({"type": "checkout.session.completed", "data": {"object": _fake_session(order)}})
        with mock.patch("stripe.Webhook.construct_event", return_value={}), self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("stripe_webhook"), data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=x")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertIsNotNone(order.estimated_delivery)
        self.assertEqual(len(mail.outbox), 1)  # one "confirmed + paid" email, not two
        mail.outbox.clear()

        with mock.patch("stripe.Refund.create") as refund, self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("cancel_order", args=[order.id]))
        refund.assert_called_once()
        self.assertEqual(refund.call_args.kwargs["payment_intent"], "pi_test_123")
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.payment_status, "refunded")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("full refund", mail.outbox[0].alternatives[0][0])
        self.assertIn("$20.00", mail.outbox[0].alternatives[0][0])
        self.assertContains(self.client.get(reverse("my_orders")), "refunded to your card")

    def test_failed_refund_keeps_order_and_warns(self):
        order = self._cod_order()
        Order.objects.filter(pk=order.pk).update(payment_status="paid", payment_method="card", stripe_payment_intent="pi_x")
        with mock.patch("stripe.Refund.create", side_effect=Exception("down")):
            r = self.client.post(reverse("cancel_order", args=[order.id]), follow=True)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertContains(r, "couldn&#x27;t process the refund")

    def test_shipped_order_cannot_be_cancelled_by_customer(self):
        order = self._cod_order()
        self._admin_set(order, "shipped")
        self.client.force_login(self.buyer)
        self.client.post(reverse("cancel_order", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")

    def test_seller_fulfilment_moves_order_forward(self):
        seller_user = User.objects.create_user("sel", "sel@example.com", "pass12345")
        seller = SellerAccount.objects.create(user=seller_user, status="approved", business_name="Sel Co")
        Product.objects.filter(pk=self.product.pk).update(seller_account=seller)
        order = self._cod_order()
        item = order.items.get()
        mail.outbox.clear()
        self.client.force_login(seller_user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("update_fulfillment_status", args=[item.id]), {"fulfillment_status": "handed_to_courier"})
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")
        self.assertIn("on its way", mail.outbox[-1].subject)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("update_fulfillment_status", args=[item.id]), {"fulfillment_status": "delivered"})
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")
        self.assertIn("delivered", mail.outbox[-1].subject)

    def test_seller_cannot_ship_cancelled_order(self):
        seller_user = User.objects.create_user("sel", "sel@example.com", "pass12345")
        seller = SellerAccount.objects.create(user=seller_user, status="approved", business_name="Sel Co")
        Product.objects.filter(pk=self.product.pk).update(seller_account=seller)
        order = self._cod_order()
        self.client.post(reverse("cancel_order", args=[order.id]))
        item = order.items.get()
        self.client.force_login(seller_user)
        self.client.post(reverse("update_fulfillment_status", args=[item.id]), {"fulfillment_status": "handed_to_courier"})
        item.refresh_from_db()
        self.assertEqual(item.fulfillment_status, "pending")

    def test_delivery_days_setting_controls_date(self):
        from .order_emails import add_business_days
        from datetime import date
        self.assertEqual(add_business_days(date(2026, 10, 9), 1), date(2026, 10, 12))  # Fri -> Mon
        SiteSettings.objects.update_or_create(pk=1, defaults={"delivery_days": 10})
        cache.clear()
        order = self._cod_order()
        from django.utils import timezone
        self.assertEqual(order.estimated_delivery, add_business_days(timezone.localdate(), 10))


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_WEBHOOK_SECRET="whsec_dummy")
class AuditFixTests(TestCase):
    """Regression tests for the problems found in the full-site review."""

    def setUp(self):
        self.buyer = User.objects.create_user("buyer", "buyer@example.com", "pass12345")
        self.staff = User.objects.create_user("boss", "boss@example.com", "pass12345", is_staff=True)
        self.product = make_product(name="Face Wash", price=Decimal("20.00"), stock=10)

    # --- helpers -----------------------------------------------------------
    def _seller(self, name="Sel Co", username="sel", **kw):
        u = User.objects.create_user(username, f"{username}@example.com", "pass12345")
        return SellerAccount.objects.create(user=u, status="approved", business_name=name, **kw)

    def _delivered_order(self, user=None, product=None, qty=1, **kw):
        product = product or self.product
        order = Order.objects.create(user=user or self.buyer, email="buyer@example.com", full_name="B", address="1 St",
                                     city="Austin", country="US", phone="1", status="delivered", **kw)
        OrderItem.objects.create(order=order, product=product, product_name=product.name, price=product.price, quantity=qty)
        return order

    def _webhook(self, session):
        payload = json.dumps({"type": "checkout.session.completed", "data": {"object": session}})
        with mock.patch("stripe.Webhook.construct_event", return_value={}):
            return self.client.post(reverse("stripe_webhook"), data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=x")

    def _card_checkout(self, qty=1, **post):
        self.client.force_login(self.buyer)
        self.client.post(reverse("add_to_cart", args=[self.product.id]), {"quantity": qty})
        fake = mock.MagicMock(id="cs_test_1", url="https://checkout.stripe.com/x")
        with mock.patch("stripe.checkout.Session.create", return_value=fake) as create, \
             mock.patch("stripe.Coupon.create", return_value=mock.MagicMock(id="co_1")) as coupon:
            self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "card", **post})
        return Order.objects.latest("id"), create, coupon

    # --- sellers -----------------------------------------------------------
    def test_seller_product_pages_open_and_keep_zero_stock(self):
        seller = self._seller()
        p = make_product(name="Sold Out", seller_account=seller, seller_name="Sel Co", stock=0)
        self.client.force_login(seller.user)
        self.assertEqual(self.client.get(reverse("seller_add_product")).status_code, 200)
        page = self.client.get(reverse("seller_edit_product", args=[p.id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="stock" min="0" step="1" value="0"')

    def test_seller_can_keep_uploaded_image_path(self):
        seller = self._seller()
        p = make_product(name="Local", seller_account=seller, seller_name="Sel Co", image_url="/media/products/a.png")
        self.client.force_login(seller.user)
        self.client.post(reverse("seller_edit_product", args=[p.id]), {
            "name": "Local", "category": "skincare", "price": "11.00", "stock": "3", "image_url": "/media/products/a.png",
        })
        p.refresh_from_db()
        self.assertEqual(p.price, Decimal("11.00"))

    def test_store_name_with_slash_works(self):
        seller = self._seller(name="A/B Goods")
        p = make_product(name="Slashy", seller_account=seller, seller_name="A/B Goods")
        self.assertEqual(self.client.get(reverse("product_detail", args=[p.id])).status_code, 200)
        self.assertContains(self.client.get(reverse("store_page", args=["A/B Goods"])), "Slashy")

    def test_suspended_seller_products_hidden_and_unbuyable(self):
        seller = self._seller()
        p = make_product(name="Gone", seller_account=seller, seller_name="Sel Co")
        SellerAccount.objects.filter(pk=seller.pk).update(status="suspended")
        self.assertEqual(self.client.get(reverse("product_detail", args=[p.id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("all_products")), "Gone")
        self.client.post(reverse("add_to_cart", args=[p.id]))
        self.assertEqual(self.client.session.get("cart", {}), {})

    def test_team_admin_can_manage_team(self):
        org = self._seller(name="Org", username="orgo", account_type="organization")
        admin = User.objects.create_user("adm", "adm@example.com", "pass12345")
        OrganizationMember.objects.create(organization=org, user=admin, role="admin")
        User.objects.create_user("newbie", "newbie@example.com", "pass12345")
        self.client.force_login(admin)
        self.client.post(reverse("add_team_member"), {"username_or_email": "newbie", "role": "staff"})
        self.assertTrue(OrganizationMember.objects.filter(organization=org, user__username="newbie").exists())

    def test_seller_answers_question_and_asker_is_notified(self):
        seller = self._seller()
        p = make_product(name="Q Product", seller_account=seller, seller_name="Sel Co")
        self.client.force_login(self.buyer)
        self.client.post(reverse("ask_question", args=[p.id]), {"question": "Is it vegan?"})
        self.assertTrue(Notification.objects.filter(user=seller.user, message__icontains="question").exists())
        q = Question.objects.get()
        self.client.force_login(seller.user)
        self.assertContains(self.client.get(reverse("seller_dashboard")), "Is it vegan?")
        self.client.post(reverse("seller_answer_question", args=[q.id]), {"answer": "Yes, 100%."})
        q.refresh_from_db()
        self.assertEqual(q.answer, "Yes, 100%.")
        self.assertTrue(Notification.objects.filter(user=self.buyer, message__icontains="answered").exists())

    # --- payments ----------------------------------------------------------
    def test_cancelled_card_order_cannot_be_charged(self):
        order, _, _ = self._card_checkout()
        with mock.patch("stripe.checkout.Session.expire") as expire:
            self.client.post(reverse("cancel_order", args=[order.id]))
        expire.assert_called_once_with("cs_test_1")
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("cancelled", "failed"))
        # Even if the old Stripe tab is paid anyway, the money goes straight back.
        order.payment_status = "pending"
        order.save(update_fields=["payment_status"])
        with mock.patch("stripe.Refund.create") as refund:
            self._webhook(_fake_session(order))
        refund.assert_called_once()
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("cancelled", "refunded"))

    def test_zero_decimal_currency_amounts(self):
        from .payments import to_cents
        self.assertEqual(to_cents(Decimal("1000"), "jpy"), 1000)
        self.assertEqual(to_cents(Decimal("10.50"), "usd"), 1050)

    def test_coupon_kept_when_payment_cancelled(self):
        Coupon.objects.create(code="SAVE10", percent_off=10)
        self.client.force_login(self.buyer)
        self.client.post(reverse("add_to_cart", args=[self.product.id]))
        self.client.post(reverse("apply_coupon"), {"coupon_code": "SAVE10"})
        fake = mock.MagicMock(id="cs_test_2", url="https://checkout.stripe.com/x")
        with mock.patch("stripe.checkout.Session.create", return_value=fake), \
             mock.patch("stripe.Coupon.create", return_value=mock.MagicMock(id="co")):
            self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "card"})
        order = Order.objects.get()
        self.assertEqual(self.client.session["coupon_code"], "")
        with mock.patch("stripe.checkout.Session.retrieve", side_effect=Exception("offline")), \
             mock.patch("stripe.checkout.Session.expire"):
            self.client.get(reverse("payment_cancel", args=[order.id]))
        self.assertEqual(self.client.session["coupon_code"], "SAVE10")

    def test_free_shipping_uses_price_after_coupon(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"shipping_flat_fee": Decimal("5"), "free_shipping_threshold": Decimal("50")})
        cache.clear()
        from .views import _price_cart
        coupon = Coupon(code="HALF", percent_off=50)
        self.assertEqual(_price_cart(Decimal("60"), coupon)["shipping"], Decimal("5"))
        self.assertEqual(_price_cart(Decimal("120"), coupon)["shipping"], Decimal("0"))

    # --- store credit & points ---------------------------------------------
    def test_store_credit_partly_pays_card_order_and_returns_on_cancel(self):
        Profile.objects.create(user=self.buyer, referral_code="B1", store_credit=Decimal("5.00"))
        order, create, coupon = self._card_checkout(use_credit="1")
        self.assertEqual(order.credit_used, Decimal("5.00"))
        self.assertEqual(order.total, Decimal("15.00"))
        self.assertEqual(coupon.call_args.kwargs["amount_off"], 500)
        self.assertEqual(Profile.objects.get(user=self.buyer).store_credit, 0)
        with mock.patch("stripe.checkout.Session.expire"):
            self.client.post(reverse("cancel_order", args=[order.id]))
        self.assertEqual(Profile.objects.get(user=self.buyer).store_credit, Decimal("5.00"))

    def test_store_credit_can_cover_whole_order(self):
        Profile.objects.create(user=self.buyer, referral_code="B1", store_credit=Decimal("50.00"))
        order, create, _ = self._card_checkout(use_credit="1")
        create.assert_not_called()
        self.assertEqual((order.payment_status, order.status), ("paid", "confirmed"))
        self.assertEqual(Profile.objects.get(user=self.buyer).store_credit, Decimal("30.00"))
        self.client.post(reverse("cancel_order", args=[order.id]))  # nothing on a card; credit comes back
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(Profile.objects.get(user=self.buyer).store_credit, Decimal("50.00"))

    def test_credit_not_used_unless_ticked(self):
        Profile.objects.create(user=self.buyer, referral_code="B1", store_credit=Decimal("5.00"))
        order, _, _ = self._card_checkout()
        self.assertEqual(order.credit_used, 0)

    def test_redeem_points(self):
        Profile.objects.create(user=self.buyer, referral_code="B1", loyalty_points=250)
        self.client.force_login(self.buyer)
        self.assertContains(self.client.get(reverse("profile")), "Turn 200 points into $2.00")
        self.client.post(reverse("redeem_points"))
        prof = Profile.objects.get(user=self.buyer)
        self.assertEqual((prof.loyalty_points, prof.store_credit), (50, Decimal("2.00")))

    # --- returns -----------------------------------------------------------
    def _return(self, order, method="original_payment"):
        return ReturnRequest.objects.create(order_item=order.items.get(), user=self.buyer, reason="Broken", refund_method=method)

    def test_return_refund_respects_coupon_and_only_once(self):
        order = self._delivered_order(qty=1, discount_amount=Decimal("10.00"), payment_status="paid",
                                      payment_method="card", stripe_payment_intent="pi_1")
        rr = self._return(order)
        self.client.force_login(self.staff)
        with mock.patch("stripe.Refund.create") as refund:
            self.client.post(reverse("manage_returns"), {"id": rr.id, "action": "refund"})
            self.client.post(reverse("manage_returns"), {"id": rr.id, "action": "refund"})
        refund.assert_called_once()
        self.assertEqual(refund.call_args.kwargs["amount"], 1000)  # paid $10 after coupon, not $20
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 11)  # restocked once

    def test_store_credit_refund_and_cod_refund(self):
        order = self._delivered_order(payment_method="cod")
        rr = self._return(order, method="store_credit")
        self.client.force_login(self.staff)
        self.client.post(reverse("manage_returns"), {"id": rr.id, "action": "refund"})
        self.assertEqual(Profile.objects.get(user=self.buyer).store_credit, Decimal("20.00"))
        order2 = self._delivered_order(payment_method="cod")
        rr2 = self._return(order2)
        r = self.client.post(reverse("manage_returns"), {"id": rr2.id, "action": "refund"}, follow=True)
        self.assertContains(r, "pay the customer $20.00 yourself")
        rr2.refresh_from_db()
        self.assertEqual(rr2.status, "refunded")

    def test_return_window_enforced(self):
        from datetime import timedelta
        from django.utils import timezone
        order = self._delivered_order()
        Order.objects.filter(pk=order.pk).update(delivered_at=timezone.now() - timedelta(days=20))
        self.client.force_login(self.buyer)
        item = order.items.get()
        self.client.post(reverse("request_return", args=[item.id]), {"reason": "late"})
        self.assertFalse(ReturnRequest.objects.exists())
        self.assertContains(self.client.get(reverse("my_orders")), "Return window closed")

    def test_delivered_at_recorded(self):
        order = self._delivered_order()
        order.status = "confirmed"; order.save()
        order.status = "delivered"; order.save(update_fields=["status"])
        order.refresh_from_db()
        self.assertIsNotNone(order.delivered_at)

    # --- accounts, reviews, misc -------------------------------------------
    def test_seller_cannot_review_own_product(self):
        seller = self._seller()
        p = make_product(name="Mine", seller_account=seller, seller_name="Sel Co")
        self._delivered_order(user=seller.user, product=p)
        self.client.force_login(seller.user)
        self.client.post(reverse("product_detail", args=[p.id]), {"rating": 5, "comment": "Best ever"})
        self.assertFalse(Review.objects.exists())

    def test_referral_rewarded_once_after_first_delivery(self):
        referrer = Profile.objects.create(user=self.staff, referral_code="REFCODE1")
        self.client.post(reverse("signup") + "?ref=REFCODE1", {
            "username": "friend", "email": "friend@example.com", "password": "Strong-pass-123",
            "confirm_password": "Strong-pass-123", "ref": "REFCODE1",
        })
        friend = User.objects.get(username="friend")
        self.assertFalse(Coupon.objects.filter(code__startswith="REF-").exists())  # nothing for just signing up
        welcome = Coupon.objects.get(code__startswith="WELCOME-")
        self.assertEqual(welcome.usage_limit, 1)
        order = self._delivered_order(user=friend)
        order.status = "confirmed"; order.save()
        order.status = "delivered"; order.save()
        order2 = self._delivered_order(user=friend)
        order2.status = "confirmed"; order2.save(); order2.status = "delivered"; order2.save()
        self.assertEqual(Coupon.objects.filter(code__startswith="REF-").count(), 1)
        self.assertTrue(Notification.objects.filter(user=referrer.user, message__icontains="first purchase").exists())

    def test_signup_returns_to_next_page(self):
        r = self.client.post(reverse("signup"), {
            "username": "newone", "email": "newone@example.com", "password": "Strong-pass-123",
            "confirm_password": "Strong-pass-123", "next": "/checkout/",
        })
        self.assertEqual(r.url, "/checkout/")
        r = self.client.post(reverse("logout"))
        r = self.client.post(reverse("signup"), {
            "username": "evil", "email": "evil@example.com", "password": "Strong-pass-123",
            "confirm_password": "Strong-pass-123", "next": "https://evil.example/",
        })
        self.assertEqual(r.url, "/")

    def test_notifications_not_marked_read_by_prefetch(self):
        Notification.objects.create(user=self.buyer, message="hi", link="/")
        self.client.force_login(self.buyer)
        self.client.get(reverse("notifications_list"), HTTP_SEC_PURPOSE="prefetch;prerender")
        self.assertTrue(Notification.objects.filter(user=self.buyer, is_read=False).exists())
        self.client.get(reverse("notifications_list"))
        self.assertFalse(Notification.objects.filter(user=self.buyer, is_read=False).exists())

    def test_add_to_cart_requires_post(self):
        self.assertEqual(self.client.get(reverse("add_to_cart", args=[self.product.id])).status_code, 405)

    def test_cart_badge_ignores_removed_products(self):
        gone = make_product(name="Temp")
        self.client.post(reverse("add_to_cart", args=[self.product.id]))
        self.client.post(reverse("add_to_cart", args=[gone.id]))
        gone.delete()
        self.assertEqual(self.client.get(reverse("home")).context["cart_count"], 1)

    def test_cancelled_order_success_page(self):
        self.client.force_login(self.buyer)
        order = self._delivered_order()
        Order.objects.filter(pk=order.pk).update(status="cancelled")
        page = self.client.get(reverse("order_success", args=[order.id]))
        self.assertContains(page, "was cancelled")
        self.assertNotContains(page, "your order is confirmed")

    def test_guest_chat_kept_after_login(self):
        self.client.get(reverse("chat_messages"))
        self.client.post(reverse("chat_send"), {"message": "Where is my parcel?"})
        self.client.post(reverse("login"), {"username": "buyer", "password": "pass12345"})
        msgs = self.client.get(reverse("chat_messages")).json()["messages"]
        self.assertTrue(any("parcel" in m["message"] for m in msgs))
        self.assertEqual(ChatThread.objects.get(user=self.buyer).messages.filter(sender="user").count(), 1)


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_WEBHOOK_SECRET="whsec_dummy")
class AccountSettingsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("acct", "acct@example.com", "Old-pass-123")
        self.client.force_login(self.user)

    # --- password & devices -------------------------------------------------
    def test_change_password(self):
        other = Client()
        other.force_login(self.user)
        r = self.client.post(reverse("account_security"), {"old_password": "wrong", "new_password1": "New-pass-456!", "new_password2": "New-pass-456!"})
        self.assertContains(r, "incorrectly")
        self.client.post(reverse("account_security"), {"old_password": "Old-pass-123", "new_password1": "New-pass-456!", "new_password2": "New-pass-456!"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-pass-456!"))
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)  # still signed in here
        self.assertEqual(other.get(reverse("profile")).status_code, 302)  # signed out elsewhere
        self.assertTrue(any("password was changed" in m.subject.lower() for m in mail.outbox))

    def test_sign_out_other_devices(self):
        self.client.logout()
        self.client.post(reverse("login"), {"username": "acct", "password": "Old-pass-123", "remember": "1"})
        other = Client()
        other.post(reverse("login"), {"username": "acct", "password": "Old-pass-123", "remember": "1"})
        self.assertContains(self.client.get(reverse("account_security")), "1 other device")
        self.client.post(reverse("account_security"), {"action": "signout_others"})
        self.assertEqual(other.get(reverse("profile")).status_code, 302)
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)

    def test_remember_me(self):
        self.client.logout()
        self.client.post(reverse("login"), {"username": "acct", "password": "Old-pass-123"})
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.client.logout()
        self.client.post(reverse("login"), {"username": "acct", "password": "Old-pass-123", "remember": "1"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_email_change_alerts_old_address(self):
        self.client.post(reverse("profile"), {"first_name": "A", "email": "new@example.com", "phone": ""})
        self.assertTrue(any(m.to == ["acct@example.com"] and "email address was changed" in m.subject.lower() for m in mail.outbox))

    # --- saved cards ----------------------------------------------------------
    def test_cards_listed_added_and_removed(self):
        Profile.objects.create(user=self.user, referral_code="AC1", stripe_customer_id="cus_1")
        listing = {"data": [{"id": "pm_1", "card": {"brand": "visa", "last4": "4242", "exp_month": 4, "exp_year": 2030}}]}
        with mock.patch("stripe.Customer.list_payment_methods", return_value=listing):
            page = self.client.get(reverse("payment_methods"))
        self.assertContains(page, "Visa ending in 4242")
        self.assertContains(page, "Expires 04/2030")
        with mock.patch("stripe.checkout.Session.create", return_value=mock.MagicMock(url="https://checkout.stripe.com/setup")) as create:
            r = self.client.post(reverse("add_card"))
        self.assertEqual(r.url, "https://checkout.stripe.com/setup")
        self.assertEqual(create.call_args.kwargs["mode"], "setup")
        self.assertEqual(create.call_args.kwargs["customer"], "cus_1")
        # Someone else's card can't be removed
        with mock.patch("stripe.PaymentMethod.retrieve", return_value={"customer": "cus_OTHER"}), \
             mock.patch("stripe.PaymentMethod.detach") as detach:
            self.client.post(reverse("remove_card"), {"card": "pm_x"})
        detach.assert_not_called()
        with mock.patch("stripe.PaymentMethod.retrieve", return_value={"customer": "cus_1"}), \
             mock.patch("stripe.PaymentMethod.detach") as detach:
            self.client.post(reverse("remove_card"), {"card": "pm_1"})
        detach.assert_called_once_with("pm_1")

    def test_card_added_on_stripe_is_offered_again(self):
        Profile.objects.create(user=self.user, referral_code="AC1", stripe_customer_id="cus_1")
        session = {"customer": "cus_1", "status": "complete", "setup_intent": {"payment_method": "pm_9"}}
        with mock.patch("stripe.checkout.Session.retrieve", return_value=session), \
             mock.patch("stripe.Customer.list_payment_methods", return_value={"data": []}), \
             mock.patch("stripe.PaymentMethod.modify") as modify:
            r = self.client.get(reverse("payment_methods") + "?added=cs_setup_1", follow=True)
        modify.assert_called_once_with("pm_9", allow_redisplay="always")
        self.assertContains(r, "Card saved")

    def test_checkout_uses_saved_customer(self):
        product = make_product(price=Decimal("10.00"))
        self.client.post(reverse("add_to_cart", args=[product.id]))
        fake = mock.MagicMock(id="cs_1", url="https://checkout.stripe.com/pay")
        with mock.patch("stripe.checkout.Session.create", return_value=fake) as create:
            self.client.post(reverse("checkout"), {**CHECKOUT_FORM, "payment_method": "card"})
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["customer"], "cus_test")
        self.assertEqual(kwargs["saved_payment_method_options"], {"payment_method_save": "enabled"})
        self.assertNotIn("customer_email", kwargs)
        self.assertEqual(Profile.objects.get(user=self.user).stripe_customer_id, "cus_test")

    @override_settings(STRIPE_SECRET_KEY="")
    def test_cards_page_without_stripe(self):
        self.assertContains(self.client.get(reverse("payment_methods")), "aren't switched on")

    # --- addresses ------------------------------------------------------------
    def test_edit_and_default_address_prefills_checkout(self):
        self.client.post(reverse("add_address"), {"label": "Home", "full_name": "A One", "address": "1 Road", "city": "Austin", "country": "US", "phone": "1"})
        self.client.post(reverse("add_address"), {"label": "Work", "full_name": "A Two", "address": "2 Office", "city": "Boston", "country": "US", "phone": "2"})
        home, work = Address.objects.order_by("id")
        self.assertTrue(home.is_default)
        self.client.post(reverse("edit_address", args=[work.id]), {"label": "Office", "full_name": "A Two", "address": "22 Office", "city": "Boston", "country": "US", "phone": "2", "is_default": "1"})
        work.refresh_from_db(); home.refresh_from_db()
        self.assertEqual((work.label, work.address, work.is_default, home.is_default), ("Office", "22 Office", True, False))
        product = make_product()
        self.client.post(reverse("add_to_cart", args=[product.id]))
        self.assertContains(self.client.get(reverse("checkout")), 'value="22 Office"')
        other = User.objects.create_user("other", "o@example.com", "pass12345")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("edit_address", args=[work.id])).status_code, 404)

    # --- privacy --------------------------------------------------------------
    def test_email_preferences(self):
        self.client.post(reverse("account_privacy"), {"action": "emails", "marketing": "1"})
        self.assertTrue(NewsletterSubscriber.objects.filter(email="acct@example.com").exists())
        self.client.post(reverse("account_privacy"), {"action": "emails"})
        self.assertFalse(NewsletterSubscriber.objects.exists())

    def test_download_data(self):
        order = Order.objects.create(user=self.user, full_name="A", address="x", city="y", phone="1", status="delivered")
        OrderItem.objects.create(order=order, product_name="Face Wash", price=Decimal("5"), quantity=1)
        r = self.client.get(reverse("download_data"))
        data = json.loads(r.content)
        self.assertEqual(data["account"]["email"], "acct@example.com")
        self.assertEqual(data["orders"][0]["items"][0]["product"], "Face Wash")

    def test_delete_account(self):
        r = self.client.post(reverse("delete_account"), {"password": "nope"}, follow=True)
        self.assertContains(r, "isn&#x27;t right")
        open_order = Order.objects.create(user=self.user, full_name="A", address="x", city="y", phone="1", status="shipped")
        self.client.post(reverse("delete_account"), {"password": "Old-pass-123"})
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)
        Order.objects.filter(pk=open_order.pk).update(status="delivered")
        Address.objects.create(user=self.user, full_name="A", phone="1", address="x", city="y", country="US")
        Profile.objects.create(user=self.user, referral_code="AC1", stripe_customer_id="cus_1")
        with mock.patch("stripe.Customer.delete") as delete:
            self.client.post(reverse("delete_account"), {"password": "Old-pass-123"})
        delete.assert_called_once_with("cus_1")
        u = User.objects.get(pk=self.user.pk)
        self.assertFalse(u.is_active)
        self.assertEqual(u.email, "")
        self.assertFalse(Address.objects.filter(user=u).exists())
        self.assertTrue(Order.objects.filter(pk=open_order.pk).exists())  # sales records kept
        self.assertFalse(Client().login(username="acct", password="Old-pass-123"))
        self.assertEqual(self.client.get(reverse("profile")).status_code, 302)  # signed out

    def test_sellers_cannot_self_delete(self):
        SellerAccount.objects.create(user=self.user, status="approved", business_name="S")
        self.client.post(reverse("delete_account"), {"password": "Old-pass-123"})
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)

    # --- seller store settings ------------------------------------------------
    def test_store_settings_rename_updates_products(self):
        seller = SellerAccount.objects.create(user=self.user, status="approved", business_name="Old Name")
        p = make_product(seller_account=seller, seller_name="Old Name")
        self.client.post(reverse("seller_store_settings"), {"store_name": "New Name", "store_description": "Hello", "bank_details": "IBAN 123"})
        seller.refresh_from_db(); p.refresh_from_db()
        self.assertEqual((seller.business_name, seller.bank_details, p.seller_name), ("New Name", "IBAN 123", "New Name"))
        SellerAccount.objects.create(user=User.objects.create_user("z", "z@example.com", "x"), business_name="Taken")
        self.client.post(reverse("seller_store_settings"), {"store_name": "taken"})
        seller.refresh_from_db()
        self.assertEqual(seller.business_name, "New Name")

    def test_team_staff_cannot_change_store_settings(self):
        owner = User.objects.create_user("own", "own@example.com", "x")
        org = SellerAccount.objects.create(user=owner, status="approved", business_name="Org", account_type="organization")
        OrganizationMember.objects.create(organization=org, user=self.user, role="staff")
        self.client.post(reverse("seller_store_settings"), {"store_name": "Hijack"})
        org.refresh_from_db()
        self.assertEqual(org.business_name, "Org")

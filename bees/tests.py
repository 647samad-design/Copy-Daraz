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
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .models import Coupon, SiteSettings


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

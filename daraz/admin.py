from django.contrib import admin
from .models import (
    Product, Review, Order, OrderItem, Wishlist, Coupon,
    ProductImage, Profile, Address, Question, NewsletterSubscriber,
    Notification, SearchLog, SellerAccount,
)


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "old_price", "discount_percent", "stock", "seller_name", "is_flash_sale")
    list_filter = ("category", "is_flash_sale")
    search_fields = ("name",)
    inlines = [ProductImageInline, ReviewInline, QuestionInline]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("username", "product", "rating", "created_at")
    list_filter = ("rating",)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "full_name", "city", "status", "total", "created_at")
    list_filter = ("status",)
    inlines = [OrderItemInline]


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "created_at")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "percent_off", "active")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("user", "label", "city", "phone")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("product", "username", "question", "answer")


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "message", "is_read", "created_at")


@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display = ("query", "count")


@admin.register(SellerAccount)
class SellerAccountAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "account_type", "status", "commission_rate", "created_at")
    list_filter = ("account_type", "status")
    actions = ["approve_sellers", "reject_sellers"]

    def approve_sellers(self, request, queryset):
        from .models import Notification
        for seller in queryset:
            seller.status = "approved"
            seller.save(update_fields=["status"])
            Notification.objects.create(
                user=seller.user,
                message="Your seller account has been approved! You can now list products.",
                link="/seller/dashboard/",
            )
        self.message_user(request, f"{queryset.count()} seller(s) approved.")
    approve_sellers.short_description = "Approve selected sellers"

    def reject_sellers(self, request, queryset):
        queryset.update(status="rejected")
        self.message_user(request, f"{queryset.count()} seller(s) rejected.")
    reject_sellers.short_description = "Reject selected sellers"

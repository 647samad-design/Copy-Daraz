from django.contrib import admin
from .models import (
    Product, Review, Order, OrderItem, Wishlist, Coupon,
    ProductImage, Profile, Address, Question, NewsletterSubscriber,
    Notification, SearchLog, SellerAccount, SellerReview, ReturnRequest, SiteSettings, AuditLog,
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
    list_display = ("name", "category", "price", "old_price", "discount_percent", "stock", "seller_name", "approval_status", "is_flash_sale")
    list_filter = ("category", "is_flash_sale", "approval_status")
    search_fields = ("name",)
    inlines = [ProductImageInline, ReviewInline, QuestionInline]
    actions = ["approve_products", "reject_products"]

    def approve_products(self, request, queryset):
        queryset.update(approval_status="approved")
        self.message_user(request, f"{queryset.count()} product(s) approved and now live.")
    approve_products.short_description = "Approve selected products (make live)"

    def reject_products(self, request, queryset):
        queryset.update(approval_status="rejected")
        self.message_user(request, f"{queryset.count()} product(s) rejected.")
    reject_products.short_description = "Reject selected products"


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
    list_display = ("display_name", "user", "account_type", "status", "commission_rate", "effective_commission_rate", "city", "country", "created_at")
    list_filter = ("account_type", "status")
    search_fields = ("business_name", "organization_name", "full_name", "user__username", "cnic")
    readonly_fields = ("created_at",)
    actions = ["approve_sellers", "reject_sellers", "suspend_sellers"]

    def approve_sellers(self, request, queryset):
        from .models import Notification, AuditLog
        for seller in queryset:
            seller.status = "approved"
            seller.save(update_fields=["status"])
            Notification.objects.create(
                user=seller.user,
                message="Your seller account has been approved! You can now list products.",
                link="/seller/dashboard/",
            )
            AuditLog.objects.create(user=request.user, action=f"Approved seller #{seller.id} ({seller.display_name})")
        self.message_user(request, f"{queryset.count()} seller(s) approved.")
    approve_sellers.short_description = "Approve selected sellers"

    def reject_sellers(self, request, queryset):
        from .models import AuditLog
        queryset.update(status="rejected")
        for seller in queryset:
            AuditLog.objects.create(user=request.user, action=f"Rejected seller #{seller.id} ({seller.display_name})")
        self.message_user(request, f"{queryset.count()} seller(s) rejected.")
    reject_sellers.short_description = "Reject selected sellers"

    def suspend_sellers(self, request, queryset):
        from .models import AuditLog
        queryset.update(status="suspended")
        for seller in queryset:
            AuditLog.objects.create(user=request.user, action=f"Suspended seller #{seller.id} ({seller.display_name})")
        self.message_user(request, f"{queryset.count()} seller(s) suspended.")
    suspend_sellers.short_description = "Suspend selected sellers"


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = ("order_item", "user", "status", "created_at")
    list_filter = ("status",)
    actions = ["mark_approved", "mark_rejected", "mark_refunded"]

    def mark_approved(self, request, queryset):
        queryset.update(status="approved")
    mark_approved.short_description = "Mark as approved"

    def mark_rejected(self, request, queryset):
        queryset.update(status="rejected")
    mark_rejected.short_description = "Mark as rejected"

    def mark_refunded(self, request, queryset):
        queryset.update(status="refunded")
    mark_refunded.short_description = "Mark as refunded"


@admin.register(SellerReview)
class SellerReviewAdmin(admin.ModelAdmin):
    list_display = ("seller", "user", "rating", "created_at")


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ("site_name", "tax_percent")

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("action", "user__username")

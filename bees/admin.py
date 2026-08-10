import csv
from django.contrib import admin
from django.http import HttpResponse
from django.db.models import Sum
from .models import (
    Product, Review, Order, OrderItem, Wishlist, Coupon,
    ProductImage, Profile, Address, Question, NewsletterSubscriber,
    Notification, SearchLog, SellerAccount, SellerReview, ReturnRequest, SiteSettings, AuditLog,
)


def export_as_csv(modeladmin, request, queryset, field_names):
    response = HttpResponse(content_type="text/csv")
    filename = f"{modeladmin.model._meta.verbose_name_plural}_export.csv".replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(field_names)
    for obj in queryset:
        row = []
        for field in field_names:
            value = getattr(obj, field, "")
            row.append(value() if callable(value) else value)
        writer.writerow(row)
    return response

admin.site.site_header = "19Bees Administration"
admin.site.site_title = "19Bees Admin"
admin.site.index_title = "Store Management"


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
    list_display = ("name", "category", "price", "old_price", "discount_percent", "stock", "stock_status", "seller_name", "approval_status", "is_flash_sale")
    list_filter = ("category", "is_flash_sale", "approval_status")
    search_fields = ("name", "seller_name", "description")
    list_editable = ("price", "stock")
    list_per_page = 50
    inlines = [ProductImageInline, ReviewInline, QuestionInline]
    actions = ["approve_products", "reject_products", "mark_flash_sale", "unmark_flash_sale", "export_products_csv"]

    def export_products_csv(self, request, queryset):
        return export_as_csv(self, request, queryset, [
            "id", "name", "category", "price", "old_price", "stock",
            "seller_name", "approval_status", "is_flash_sale",
        ])
    export_products_csv.short_description = "Export selected products to CSV"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        action = "Edited" if change else "Created"
        AuditLog.objects.create(user=request.user, action=f"{action} product #{obj.id} ({obj.name})")

    def delete_model(self, request, obj):
        AuditLog.objects.create(user=request.user, action=f"Deleted product #{obj.id} ({obj.name})")
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            AuditLog.objects.create(user=request.user, action=f"Deleted product #{obj.id} ({obj.name})")
        super().delete_queryset(request, queryset)

    def stock_status(self, obj):
        if obj.stock <= 0:
            return "Out of stock"
        if obj.stock <= 5:
            return f"Low ({obj.stock})"
        return "In stock"
    stock_status.short_description = "Stock status"

    def approve_products(self, request, queryset):
        queryset.update(approval_status="approved")
        self.message_user(request, f"{queryset.count()} product(s) approved and now live.")
    approve_products.short_description = "Approve selected products (make live)"

    def reject_products(self, request, queryset):
        queryset.update(approval_status="rejected")
        self.message_user(request, f"{queryset.count()} product(s) rejected.")
    reject_products.short_description = "Reject selected products"

    def mark_flash_sale(self, request, queryset):
        queryset.update(is_flash_sale=True)
        self.message_user(request, f"{queryset.count()} product(s) added to flash sale.")
    mark_flash_sale.short_description = "Add to flash sale"

    def unmark_flash_sale(self, request, queryset):
        queryset.update(is_flash_sale=False)
        self.message_user(request, f"{queryset.count()} product(s) removed from flash sale.")
    unmark_flash_sale.short_description = "Remove from flash sale"


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("username", "product", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("username", "product__name", "comment")
    date_hierarchy = "created_at"


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("product", "product_name", "price", "quantity")
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "full_name", "city", "phone", "payment_method", "status", "tracking_number", "total", "created_at")
    list_filter = ("status", "payment_method", "created_at")
    search_fields = ("id", "full_name", "user__username", "phone", "city", "tracking_number")
    list_editable = ("status",)
    fields = (
        "user", "guest_email", "full_name", "address", "city", "phone",
        "payment_method", "status", "tracking_number", "courier_name", "estimated_delivery",
        "coupon_code", "discount_amount",
    )
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]
    actions = ["mark_confirmed", "mark_shipped", "mark_delivered", "mark_cancelled", "export_orders_csv"]

    def export_orders_csv(self, request, queryset):
        return export_as_csv(self, request, queryset, [
            "id", "user", "full_name", "city", "phone", "payment_method", "status", "total", "created_at",
        ])
    export_orders_csv.short_description = "Export selected orders to CSV"

    def save_model(self, request, obj, form, change):
        old_status = None
        if change:
            old_status = Order.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
        super().save_model(request, obj, form, change)
        if change and old_status and old_status != obj.status:
            AuditLog.objects.create(
                user=request.user,
                action=f"Changed order #{obj.id} status from {old_status} to {obj.status}",
            )

    def mark_confirmed(self, request, queryset):
        self._bulk_status(request, queryset, "confirmed")
    mark_confirmed.short_description = "Mark selected orders as Confirmed"

    def mark_shipped(self, request, queryset):
        self._bulk_status(request, queryset, "shipped")
    mark_shipped.short_description = "Mark selected orders as Shipped"

    def mark_delivered(self, request, queryset):
        self._bulk_status(request, queryset, "delivered")
    mark_delivered.short_description = "Mark selected orders as Delivered"

    def mark_cancelled(self, request, queryset):
        self._bulk_status(request, queryset, "cancelled")
    mark_cancelled.short_description = "Mark selected orders as Cancelled"

    def _bulk_status(self, request, queryset, new_status):
        for order in queryset:
            old_status = order.status
            if old_status != new_status:
                AuditLog.objects.create(
                    user=request.user,
                    action=f"Changed order #{order.id} status from {old_status} to {new_status}",
                )
        queryset.update(status=new_status)


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "created_at")
    search_fields = ("user__username", "product__name")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "percent_off", "active")
    list_editable = ("active",)
    search_fields = ("code",)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone")
    search_fields = ("user__username", "phone")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("user", "label", "city", "phone")
    search_fields = ("user__username", "city", "phone")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("product", "username", "question", "answer")
    search_fields = ("product__name", "username", "question")


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at")
    search_fields = ("email",)
    date_hierarchy = "created_at"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "message", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("user__username", "message")


@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display = ("query", "count")
    search_fields = ("query",)
    ordering = ("-count",)


@admin.register(SellerAccount)
class SellerAccountAdmin(admin.ModelAdmin):
    list_display = (
        "display_name", "user", "account_type", "status",
        "products_sold_count", "total_sales_display", "commission_owed_display",
        "net_earnings_display", "total_paid_out", "amount_owed_display",
        "commission_rate", "effective_commission_rate", "city", "country", "created_at",
    )
    list_filter = ("account_type", "status")
    search_fields = ("business_name", "organization_name", "full_name", "user__username", "cnic")
    readonly_fields = (
        "created_at", "products_sold_count", "total_sales_display",
        "commission_owed_display", "net_earnings_display", "amount_owed_display",
    )
    date_hierarchy = "created_at"
    actions = ["approve_sellers", "reject_sellers", "suspend_sellers", "mark_fully_paid_out", "export_sellers_csv"]

    def export_sellers_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="sellers_export.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "id", "display_name", "username", "account_type", "status", "units_sold",
            "total_sales", "commission_rate_pct", "commission_owed", "net_earnings",
            "total_paid_out", "still_owed", "city", "country", "created_at",
        ])
        for s in queryset:
            units = OrderItem.objects.filter(product__seller_account=s).aggregate(
                total=Sum("quantity")
            )["total"] or 0
            sales = float(s.lifetime_sales)
            rate = s.effective_commission_rate
            commission = round(sales * rate / 100, 2)
            writer.writerow([
                s.id, s.display_name, s.user.username, s.account_type, s.status, units,
                f"{sales:.2f}", rate, f"{commission:.2f}", f"{s.net_earnings:.2f}",
                f"{s.total_paid_out:.2f}", f"{s.amount_owed:.2f}", s.city, s.country, s.created_at,
            ])
        return response
    export_sellers_csv.short_description = "Export selected sellers to CSV"

    fieldsets = (
        ("Account", {"fields": ("user", "account_type", "status", "commission_rate", "admin_note", "created_at")}),
        ("Earnings summary", {"fields": (
            "products_sold_count", "total_sales_display", "commission_owed_display",
            "net_earnings_display", "total_paid_out", "amount_owed_display",
        )}),
        ("Registration details", {"fields": (
            "full_name", "business_name", "organization_name", "phone", "cnic",
            "business_address", "city", "country", "store_description",
            "product_categories", "brand_info", "tax_info", "bank_details",
        )}),
        ("Documents", {"fields": ("business_certificate", "id_document", "store_logo", "store_banner")}),
    )

    def products_sold_count(self, obj):
        from django.db.models import Sum
        total = OrderItem.objects.filter(product__seller_account=obj).aggregate(total=Sum("quantity"))["total"]
        return total or 0
    products_sold_count.short_description = "Units sold"

    def total_sales_display(self, obj):
        return f"Rs.{obj.lifetime_sales:.2f}"
    total_sales_display.short_description = "Total sales"

    def commission_owed_display(self, obj):
        sales = float(obj.lifetime_sales)
        rate = obj.effective_commission_rate
        owed = sales * rate / 100
        return f"Rs.{owed:.2f} ({rate:g}%)"
    commission_owed_display.short_description = "Commission owed"

    def net_earnings_display(self, obj):
        return f"Rs.{obj.net_earnings:.2f}"
    net_earnings_display.short_description = "Seller's net earnings (after commission)"

    def amount_owed_display(self, obj):
        return f"Rs.{obj.amount_owed:.2f}"
    amount_owed_display.short_description = "Still owed to seller"

    def mark_fully_paid_out(self, request, queryset):
        count = 0
        for seller in queryset:
            seller.total_paid_out = seller.net_earnings
            seller.save(update_fields=["total_paid_out"])
            AuditLog.objects.create(
                user=request.user,
                action=f"Recorded full payout of Rs.{seller.net_earnings:.2f} to seller #{seller.id} ({seller.display_name})",
            )
            count += 1
        self.message_user(request, f"Marked {count} seller(s) as fully paid out.")
    mark_fully_paid_out.short_description = "Mark selected sellers as fully paid out"

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
    list_display = ("order_item", "user", "status", "refund_method", "created_at")
    list_filter = ("status", "refund_method")
    search_fields = ("user__username",)
    date_hierarchy = "created_at"
    actions = ["mark_approved", "mark_rejected", "mark_refunded"]

    def mark_approved(self, request, queryset):
        self._bulk_status(queryset, "approved")
    mark_approved.short_description = "Mark as approved"

    def mark_rejected(self, request, queryset):
        self._bulk_status(queryset, "rejected")
    mark_rejected.short_description = "Mark as rejected"

    def mark_refunded(self, request, queryset):
        self._bulk_status(queryset, "refunded")
    mark_refunded.short_description = "Mark as refunded"

    def _bulk_status(self, queryset, new_status):
        # Loop + individual .save() so the model's save() override fires
        # (notifies the customer) - queryset.update() would skip that.
        for obj in queryset:
            obj.status = new_status
            obj.save(update_fields=["status"])


@admin.register(SellerReview)
class SellerReviewAdmin(admin.ModelAdmin):
    list_display = ("seller", "user", "rating", "created_at")
    search_fields = ("seller__display_name", "user__username")


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
    date_hierarchy = "created_at"

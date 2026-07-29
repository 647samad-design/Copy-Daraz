from django.contrib import admin
from .models import Product, Review


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "old_price", "discount_percent", "is_flash_sale")
    list_filter = ("category", "is_flash_sale")
    search_fields = ("name",)
    inlines = [ReviewInline]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("username", "product", "rating", "created_at")
    list_filter = ("rating",)

from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Product


class ProductSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return Product.objects.filter(approval_status="approved")

    def location(self, obj):
        return reverse("product_detail", args=[obj.id])


class CategorySitemap(Sitemap):
    changefreq = "daily"
    priority = 0.7

    def items(self):
        # Pulled directly from the model so this never goes stale if
        # categories are added or renamed.
        return [choice[0] for choice in Product.CATEGORY_CHOICES]

    def location(self, item):
        return reverse("category_products", args=[item])


class StaticViewSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.5

    def items(self):
        return ["home", "all_products", "help_support", "about_us", "terms_page", "privacy_page"]

    def location(self, item):
        return reverse(item)

from django.core.management.base import BaseCommand
from daraz.models import Product


class Command(BaseCommand):
    help = "Seed the database with sample Copy-Daraz products"

    def handle(self, *args, **options):
        products = [
            {
                "name": "Jenpharm Dermive Oil Free Moisturizer 100ml",
                "image_url": "https://picsum.photos/id/26/400/400",
                "price": 940, "old_price": 1098, "discount_percent": 14,
                "category": "skincare", "is_flash_sale": True,
                "description": "Oil free moisturizer with ceramides and hyaluronic acid, suitable for men and women.",
            },
            {
                "name": "Jenpharm Spectrablock Max SPF100 Tinted 40gm",
                "image_url": "https://picsum.photos/id/27/400/400",
                "price": 861, "old_price": 998, "discount_percent": 14,
                "category": "skincare", "is_flash_sale": True,
                "description": "Tinted super sunscreen, dermatologically tested and lightweight.",
            },
            {
                "name": "Dove Intense Repair Shampoo 175ML",
                "image_url": "https://picsum.photos/id/28/400/400",
                "price": 529, "old_price": 550, "discount_percent": 4,
                "category": "haircare", "is_flash_sale": True,
                "description": "Repairs damage and reduces hair breakage with every wash.",
            },
            {
                "name": "L'Oreal Paris Elvive Hyaluron Moisture Shampoo",
                "image_url": "https://picsum.photos/id/29/400/400",
                "price": 819, "old_price": 1050, "discount_percent": 22,
                "category": "haircare", "is_flash_sale": True,
                "description": "72H hydration and 2x plumper hair, salon quality expert hair care.",
            },
            {
                "name": "Sufi Sunflower Cooking Oil 1Ltr x 5 Poly Bags",
                "image_url": "https://picsum.photos/id/30/400/400",
                "price": 3088, "old_price": 3090, "discount_percent": 0,
                "category": "grocery", "is_flash_sale": True,
                "description": "Sunflower cooking oil enriched with vitamins A and D.",
            },
            {
                "name": "New Trendy DTF T-Shirt and Trouser Set",
                "image_url": "https://picsum.photos/id/31/400/400",
                "price": 783, "old_price": 3599, "discount_percent": 78,
                "category": "fashion", "is_flash_sale": True,
                "description": "Trendy strong DTF printed t-shirt and trouser combo set for men.",
            },
            {
                "name": "Wireless Bluetooth Earbuds",
                "image_url": "https://picsum.photos/id/48/400/400",
                "price": 1499, "old_price": 2500, "discount_percent": 40,
                "category": "electronics", "is_flash_sale": False,
                "description": "High quality wireless earbuds with noise cancellation and long battery life.",
            },
            {
                "name": "Smart Fitness Watch",
                "image_url": "https://picsum.photos/id/60/400/400",
                "price": 2999, "old_price": 4500, "discount_percent": 33,
                "category": "electronics", "is_flash_sale": False,
                "description": "Track your heart rate, steps and sleep with this smart fitness watch.",
            },
        ]

        for p in products:
            obj, created = Product.objects.get_or_create(name=p["name"], defaults=p)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created: {obj.name}"))
            else:
                self.stdout.write(f"Already exists: {obj.name}")

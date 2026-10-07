import random
from django.core.management.base import BaseCommand
from bees.models import Product, Coupon, ProductImage

CATEGORY_PRODUCTS = {
    "skincare": ["Oil-Free Moisturizer 100ml", "Vitamin C Serum 30ml", "Sunblock SPF 50", "Aloe Vera Gel 200ml", "Charcoal Face Wash"],
    "haircare": ["Anti-Dandruff Shampoo", "Argan Oil Hair Serum", "Keratin Conditioner", "Hair Growth Oil", "Curl Defining Cream"],
    "grocery": ["Sunflower Cooking Oil 1L", "Basmati Rice 5kg", "Brown Lentils 1kg", "Green Tea 100 Bags", "Honey 500g"],
    "fashion": ["Men's Casual T-Shirt", "Women's Linen Tunic", "Denim Jacket", "Formal Trouser", "Printed Scarf"],
    "electronics": ["Wireless Earbuds", "Smart Fitness Watch", "Bluetooth Speaker", "Power Bank 20000mAh", "LED Desk Lamp"],
    "3d-printers": ["Mini 3D Printer", "PLA Filament 1kg", "3D Printer Nozzle Set", "Resin 3D Printer", "3D Printer Bed Sheet"],
    "pasta-tools": ["Pasta Roller Machine", "Pizza Cutter Wheel", "Noodle Maker", "Dough Scraper Set", "Pizza Stone"],
    "sim-devices": ["Dual SIM Adapter", "SIM Card Tray Pin", "SIM Card Reader", "4G SIM Router", "eSIM Converter Kit"],
    "screen-protector": ["Tempered Glass Protector", "Privacy Screen Guard", "Matte Screen Film", "Camera Lens Protector", "Anti-Glare Film"],
    "casserole-pot": ["Ceramic Casserole Pot", "Non-Stick Cooking Pot", "Insulated Hot Pot", "Stainless Steel Casserole", "Clay Cooking Pot"],
    "table-lamp": ["LED Stage Table Lamp", "Touch Control Lamp", "Wooden Desk Lamp", "Rechargeable Reading Lamp", "Vintage Table Lamp"],
    "hoodies": ["Men's Zipper Hoodie", "Women's Fleece Hoodie", "Oversized Sweatshirt", "Kids Hoodie", "Pullover Hoodie"],
    "toy-boxes": ["Foldable Toy Box", "Stackable Storage Bins", "Kids Organizer Basket", "Canvas Storage Box", "Wooden Toy Chest"],
    "sneakers": ["Men's Running Sneakers", "Women's Casual Sneakers", "Kids Sport Shoes", "High-Top Sneakers", "Slip-On Sneakers"],
    "education": ["Kids Learning Tablet", "Alphabet Flash Cards", "School Stationery Set", "Whiteboard with Markers", "Educational Puzzle Set"],
    "dress-up-kits": ["Princess Dress-Up Set", "Superhero Costume Kit", "Doctor Role-Play Kit", "Pirate Costume Set", "Fairy Tale Dress-Up Box"],
    "microphones": ["USB Condenser Microphone", "Wireless Lapel Mic", "Karaoke Microphone", "Podcast Mic with Stand", "Bluetooth Mini Mic"],
    "leashes": ["Adjustable Dog Leash", "Cat Harness and Leash Set", "Retractable Pet Leash", "Padded Dog Collar", "Reflective Night Leash"],
    "donate-education": ["School Bag Donation Pack", "Book Donation Bundle", "Stationery Donation Kit", "Uniform Donation Set", "Learning Kit Donation"],
    "coloring-drawing": ["Coloring Book Set", "72-Color Marker Set", "Watercolor Paint Kit", "Sketch Pad A4", "Doodle Art Kit"],
    "lotion-cream": ["Hand and Foot Cream", "Body Lotion 400ml", "Whitening Scrub Cream", "Shea Butter Moisturizer", "Anti-Aging Night Cream"],
}


DESCRIPTIONS = {
    "skincare": "Gentle, dermatologist-tested formula for everyday use. Suitable for most skin types.",
    "haircare": "Salon-quality care that leaves hair soft, shiny and easy to manage.",
    "grocery": "Pantry staple, carefully sourced and packed fresh.",
    "fashion": "Comfortable everyday fit in breathable fabric. Machine washable.",
    "electronics": "Reliable everyday tech with a one-year warranty.",
    "lotion-cream": "Rich, fast-absorbing care that keeps skin soft all day.",
}


def _demo_image(name, category, variant=0):
    """Creates (once) and returns the URL of the demo packaging image."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from django.utils.text import slugify
    from bees.product_art import render_bytes

    path = f"demo/{slugify(name)}{'-' + str(variant + 1) if variant else ''}.webp"
    if not default_storage.exists(path):
        default_storage.save(path, ContentFile(render_bytes(name, category, variant=variant)))
    return default_storage.url(path)


def _is_placeholder(url):
    return not url or "picsum.photos" in url


class Command(BaseCommand):
    help = "Add the demo catalogue (with matching product images) and starter coupons. Safe to run again."

    def handle(self, *args, **options):
        created_count = updated_images = 0
        sellers = ["Official Store", "UrbanStyle Co.", "TechHub Official", "Home Essentials", "Green Pantry"]
        rng = random.Random(42)

        for category, names in CATEGORY_PRODUCTS.items():
            for i, name in enumerate(names):
                price = rng.choice([9.99, 14.99, 19.99, 24.99, 29.99, 39.99, 49.99, 69.99, 89.99, 129.00])
                has_discount = rng.choice([True, True, False])
                old_price = round(price * rng.uniform(1.15, 1.6), 2) if has_discount else None
                discount = round((1 - price / old_price) * 100) if old_price else 0
                description = DESCRIPTIONS.get(category, f"A quality pick from our {category.replace('-', ' ')} collection, chosen for everyday value.")
                obj, created = Product.objects.get_or_create(name=name, defaults={
                    "image_url": "",
                    "price": price,
                    "old_price": old_price,
                    "discount_percent": discount,
                    "category": category,
                    "is_flash_sale": has_discount and i < 2,
                    "seller_name": rng.choice(sellers),
                    "description": f"{name}. {description}",
                })
                if created:
                    created_count += 1
                # Give demo products a picture that matches them (also fixes
                # older demo data that used random stock photos).
                if _is_placeholder(obj.image_url):
                    obj.image_url = _demo_image(name, category)
                    obj.save(update_fields=["image_url"])
                    updated_images += 1
                    obj.extra_images.filter(image_url__contains="picsum.photos").delete()
                    if not obj.extra_images.exists():
                        ProductImage.objects.create(product=obj, image_url=_demo_image(name, category, variant=1))

        self.stdout.write(self.style.SUCCESS(f"Demo catalogue ready: {created_count} new products, {updated_images} product images created."))

        for code, pct in [("WELCOME10", 10), ("SAVE20", 20)]:
            _, created = Coupon.objects.get_or_create(code=code, defaults={"percent_off": pct, "active": True})
            if created:
                self.stdout.write(self.style.SUCCESS(f"Coupon created: {code} ({pct}% off)"))

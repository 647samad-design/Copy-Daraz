import random
from django.core.management.base import BaseCommand
from daraz.models import Product, Coupon, ProductImage

CATEGORY_PRODUCTS = {
    "skincare": ["Oil-Free Moisturizer 100ml", "Vitamin C Serum 30ml", "Sunblock SPF 50", "Aloe Vera Gel 200ml", "Charcoal Face Wash"],
    "haircare": ["Anti-Dandruff Shampoo", "Argan Oil Hair Serum", "Keratin Conditioner", "Hair Growth Oil", "Curl Defining Cream"],
    "grocery": ["Sunflower Cooking Oil 1L", "Basmati Rice 5kg", "Brown Lentils 1kg", "Green Tea 100 Bags", "Honey 500g"],
    "fashion": ["Men's Casual T-Shirt", "Women's Kurta", "Denim Jacket", "Formal Trouser", "Printed Scarf"],
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


class Command(BaseCommand):
    help = "Seed the database with sample 19Bees products (5+ per category)"

    def handle(self, *args, **options):
        img_id = 20
        created_count = 0

        SELLERS = ["19Bees Mall", "UrbanStyle Store", "TechHub Official", "HomeEssentials Shop", "GreenGrocer PK"]

        for category, names in CATEGORY_PRODUCTS.items():
            for i, name in enumerate(names):
                price = random.choice([299, 499, 699, 819, 940, 1250, 1699, 2199, 2999, 3499])
                has_discount = random.choice([True, True, False])
                old_price = round(price * random.uniform(1.1, 1.6)) if has_discount else None
                discount = round((1 - price / old_price) * 100) if old_price else 0
                img_id += 1

                defaults = {
                    "image_url": f"https://picsum.photos/id/{(img_id % 300) + 5}/400/400",
                    "price": price,
                    "old_price": old_price,
                    "discount_percent": discount,
                    "category": category,
                    "is_flash_sale": has_discount and i < 2,
                    "seller_name": random.choice(SELLERS),
                    "description": f"{name} - a quality pick from our {category.replace('-', ' ')} collection, chosen for everyday value and reliability.",
                }
                obj, created = Product.objects.get_or_create(name=name, defaults=defaults)
                if created:
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f"Created: {obj.name} ({category})"))
                    for extra in range(2):
                        ProductImage.objects.create(
                            product=obj,
                            image_url=f"https://picsum.photos/id/{((img_id + extra + 1) % 300) + 5}/400/400",
                        )

        self.stdout.write(self.style.SUCCESS(f"\nDone. {created_count} new products created."))

        for code, pct in [("WELCOME10", 10), ("SAVE20", 20), ("FLASH50", 50)]:
            _, created = Coupon.objects.get_or_create(code=code, defaults={"percent_off": pct, "active": True})
            if created:
                self.stdout.write(self.style.SUCCESS(f"Coupon created: {code} (-{pct}%)"))

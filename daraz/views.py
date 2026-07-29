from django.shortcuts import render, get_object_or_404, redirect
from .models import Product, Review


def home(request):
    flash_sale_products = Product.objects.filter(is_flash_sale=True)[:6]
    just_for_you_products = Product.objects.all()[:8]
    categories = Product.CATEGORY_CHOICES
    return render(request, "daraz/index.html", {
        "flash_sale_products": flash_sale_products,
        "just_for_you_products": just_for_you_products,
        "categories": categories,
    })


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if request.method == "POST":
        Review.objects.create(
            product=product,
            username=request.POST.get("username") or "Anonymous",
            rating=int(request.POST.get("rating", 5)),
            comment=request.POST.get("comment", ""),
        )
        return redirect("product_detail", pk=pk)

    reviews = product.reviews.all()
    return render(request, "daraz/product_detail.html", {
        "product": product,
        "reviews": reviews,
    })


def category_products(request, category):
    products = Product.objects.filter(category=category)
    return render(request, "daraz/category.html", {
        "products": products,
        "category": category,
    })

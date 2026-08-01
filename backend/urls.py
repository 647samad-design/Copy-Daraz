from django.contrib import admin
from django.urls import path
from daraz import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('search/', views.search_products, name='search_products'),
    path('products/', views.all_products, name='all_products'),
    path('product/<int:pk>/', views.product_detail, name='product_detail'),
    path('category/<str:category>/', views.category_products, name='category_products'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('auth/google/', views.google_auth, name='google_auth'),
    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:pk>/', views.add_to_cart, name='add_to_cart'),
    path('cart/update/<int:pk>/', views.update_cart_item, name='update_cart_item'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('checkout/coupon/', views.apply_coupon, name='apply_coupon'),
    path('order/success/<int:order_id>/', views.order_success, name='order_success'),
    path('order/cancel/<int:order_id>/', views.cancel_order, name='cancel_order'),
    path('my-orders/', views.my_orders, name='my_orders'),
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:pk>/', views.toggle_wishlist, name='toggle_wishlist'),
    path('set-language/<str:lang_code>/', views.set_language, name='set_language'),
    path('help/', views.help_support, name='help_support'),
    path('sell/', views.sell_on_daraz, name='sell_on_daraz'),
    path('about/', views.about_us, name='about_us'),
    path('terms/', views.terms_page, name='terms_page'),
    path('privacy/', views.privacy_page, name='privacy_page'),
]

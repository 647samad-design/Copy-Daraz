from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
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

    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='daraz/auth/password_reset_form.html',
        email_template_name='daraz/auth/password_reset_email.html',
        html_email_template_name='daraz/emails/password_reset.html',
        subject_template_name='daraz/auth/password_reset_subject.txt',
        success_url='/password-reset/done/',
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='daraz/auth/password_reset_done.html',
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='daraz/auth/password_reset_confirm.html',
        success_url='/reset/done/',
    ), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='daraz/auth/password_reset_complete.html',
    ), name='password_reset_complete'),

    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:pk>/', views.add_to_cart, name='add_to_cart'),
    path('cart/update/<int:pk>/', views.update_cart_item, name='update_cart_item'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('checkout/coupon/', views.apply_coupon, name='apply_coupon'),
    path('order/success/<int:order_id>/', views.order_success, name='order_success'),
    path('order/cancel/<int:order_id>/', views.cancel_order, name='cancel_order'),
    path('my-orders/', views.my_orders, name='my_orders'),
    path('order/<int:pk>/cancel/', views.cancel_order, name='cancel_order'),
    path('order-item/<int:item_id>/return/', views.request_return, name='request_return'),
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:pk>/', views.toggle_wishlist, name='toggle_wishlist'),
    path('set-language/<str:lang_code>/', views.set_language, name='set_language'),
    path('help/', views.help_support, name='help_support'),
    path('sell/', views.sell_on_daraz, name='sell_on_daraz'),
    path('about/', views.about_us, name='about_us'),
    path('terms/', views.terms_page, name='terms_page'),
    path('privacy/', views.privacy_page, name='privacy_page'),

    path('profile/', views.profile_view, name='profile'),
    path('profile/address/add/', views.add_address, name='add_address'),
    path('profile/address/delete/<int:pk>/', views.delete_address, name='delete_address'),
    path('store/<str:seller_name>/', views.store_page, name='store_page'),
    path('newsletter/subscribe/', views.newsletter_subscribe, name='newsletter_subscribe'),
    path('product/<int:pk>/ask/', views.ask_question, name='ask_question'),

    path('verify-email/<uidb64>/<token>/', views.verify_email, name='verify_email'),
    path('resend-verification/', views.resend_verification, name='resend_verification'),
    path('order/<int:order_id>/invoice/', views.invoice_pdf, name='invoice_pdf'),
    path('notifications/', views.notifications_list, name='notifications_list'),
    path('compare/', views.compare_page, name='compare_page'),
    path('compare/toggle/<int:pk>/', views.toggle_compare, name='toggle_compare'),
    path('cart/bulk-remove/', views.cart_bulk_remove, name='cart_bulk_remove'),
    path('api/search-suggest/', views.search_suggest, name='search_suggest'),

    path('become-seller/', views.become_seller, name='become_seller'),
    path('seller/dashboard/', views.seller_dashboard, name='seller_dashboard'),
    path('seller/order-item/<int:item_id>/status/', views.update_fulfillment_status, name='update_fulfillment_status'),
    path('seller/product/add/', views.seller_add_product, name='seller_add_product'),
    path('seller/product/<int:pk>/edit/', views.seller_edit_product, name='seller_edit_product'),
    path('seller/product/<int:pk>/delete/', views.seller_delete_product, name='seller_delete_product'),
    path('product/<int:pk>/quick/', views.product_quick_view, name='product_quick_view'),
    path('owner/dashboard/', views.owner_dashboard, name='owner_dashboard'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

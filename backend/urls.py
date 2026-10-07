from django.contrib import admin
from django.urls import path, re_path
from django.views.static import serve
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.contrib.sitemaps.views import sitemap
from django.views.generic import TemplateView
from bees import views
from bees import account_views
from bees import manage_views as mv
from bees.sitemaps import ProductSitemap, CategorySitemap, StaticViewSitemap
from bees.ratelimit import ratelimit

sitemaps = {
    "products": ProductSitemap,
    "categories": CategorySitemap,
    "static": StaticViewSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path('robots.txt', TemplateView.as_view(template_name="robots.txt", content_type="text/plain"), name='robots'),
    path('', views.home, name='home'),
    path('search/', views.search_products, name='search_products'),
    path('products/', views.all_products, name='all_products'),
    path('product/<int:pk>/', views.product_detail, name='product_detail'),
    path('category/<str:category>/', views.category_products, name='category_products'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    path('password-reset/', ratelimit("password_reset", rate_limit=5, window_seconds=300,
        redirect_to="password_reset", message="Too many reset requests. Please wait a few minutes and try again.")(auth_views.PasswordResetView.as_view(
        template_name='bees/auth/password_reset_form.html',
        email_template_name='bees/auth/password_reset_email.html',
        html_email_template_name='bees/emails/password_reset.html',
        subject_template_name='bees/auth/password_reset_subject.txt',
        success_url='/password-reset/done/',
    )), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='bees/auth/password_reset_done.html',
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='bees/auth/password_reset_confirm.html',
        success_url='/reset/done/',
    ), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='bees/auth/password_reset_complete.html',
    ), name='password_reset_complete'),

    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<int:pk>/', views.add_to_cart, name='add_to_cart'),
    path('cart/update/<str:key>/', views.update_cart_item, name='update_cart_item'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('checkout/coupon/', views.apply_coupon, name='apply_coupon'),
    path('order/success/<int:order_id>/', views.order_success, name='order_success'),
    path('my-orders/', views.my_orders, name='my_orders'),
    path('order/<int:order_id>/buy-again/', views.buy_again, name='buy_again'),
    path('order/<int:pk>/cancel/', views.cancel_order, name='cancel_order'),
    path('order-item/<int:item_id>/return/', views.request_return, name='request_return'),
    path('wishlist/', views.wishlist_view, name='wishlist'),
    path('wishlist/toggle/<int:pk>/', views.toggle_wishlist, name='toggle_wishlist'),
    path('set-language/<str:lang_code>/', views.set_language, name='set_language'),
    path('help/', views.help_support, name='help_support'),
    path('sell/', views.sell_on_bees, name='sell_on_bees'),
    path('about/', views.about_us, name='about_us'),
    path('terms/', views.terms_page, name='terms_page'),
    path('privacy/', views.privacy_page, name='privacy_page'),

    path('profile/', views.profile_view, name='profile'),
    path('profile/redeem-points/', views.redeem_points, name='redeem_points'),
    path('account/security/', account_views.security, name='account_security'),
    path('account/payment-methods/', account_views.payment_methods, name='payment_methods'),
    path('account/payment-methods/add/', account_views.add_card, name='add_card'),
    path('account/payment-methods/remove/', account_views.remove_card, name='remove_card'),
    path('account/privacy/', account_views.privacy, name='account_privacy'),
    path('account/privacy/download/', account_views.download_data, name='download_data'),
    path('account/delete/', account_views.delete_account, name='delete_account'),
    path('profile/address/<int:pk>/edit/', account_views.edit_address, name='edit_address'),
    path('profile/address/<int:pk>/default/', account_views.default_address, name='default_address'),
    path('seller/settings/', account_views.store_settings, name='seller_store_settings'),
    path('profile/address/add/', views.add_address, name='add_address'),
    path('profile/address/delete/<int:pk>/', views.delete_address, name='delete_address'),
    path('store/<path:seller_name>/', views.store_page, name='store_page'),
    path('newsletter/subscribe/', views.newsletter_subscribe, name='newsletter_subscribe'),
    path('product/<int:pk>/ask/', views.ask_question, name='ask_question'),
    path('product/<int:pk>/notify/', views.stock_alert, name='stock_alert'),

    path('verify-email/', views.verify_email_code, name='verify_email'),
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
    path('seller/question/<int:pk>/answer/', views.seller_answer_question, name='seller_answer_question'),
    path('seller/team/add/', views.add_team_member, name='add_team_member'),
    path('seller/team/<int:member_id>/remove/', views.remove_team_member, name='remove_team_member'),
    path('seller/product/<int:pk>/edit/', views.seller_edit_product, name='seller_edit_product'),
    path('seller/product/<int:pk>/delete/', views.seller_delete_product, name='seller_delete_product'),
    path('product/<int:pk>/quick/', views.product_quick_view, name='product_quick_view'),
    path('owner/dashboard/', views.owner_dashboard, name='owner_dashboard'),
    path('api/chat/messages/', views.chat_messages, name='chat_messages'),
    path('api/chat/send/', views.chat_send, name='chat_send'),

    path('manage/', mv.dashboard, name='manage_dashboard'),
    path('manage/orders/', mv.orders, name='manage_orders'),
    path('manage/orders/<int:pk>/', mv.order_detail, name='manage_order'),
    path('manage/products/', mv.products, name='manage_products'),
    path('manage/products/bulk/', mv.products_bulk, name='manage_products_bulk'),
    path('manage/products/new/', mv.product_form, name='manage_product_new'),
    path('manage/products/<int:pk>/', mv.product_form, name='manage_product_edit'),
    path('manage/sellers/', mv.sellers, name='manage_sellers'),
    path('manage/sellers/<int:pk>/', mv.seller_detail, name='manage_seller'),
    path('manage/customers/', mv.customers, name='manage_customers'),
    path('manage/coupons/', mv.coupons, name='manage_coupons'),
    path('manage/shipping/', mv.shipping, name='manage_shipping'),
    path('manage/reviews/', mv.reviews, name='manage_reviews'),
    path('manage/returns/', mv.returns, name='manage_returns'),
    path('manage/support/', mv.support, name='manage_support'),
    path('manage/support/<int:pk>/', mv.support, name='manage_support_thread'),
    path('manage/settings/', mv.store_settings, name='manage_settings'),

    path('payment/success/', views.payment_success, name='payment_success'),
    path('payment/cancel/<int:order_id>/', views.payment_cancel, name='payment_cancel'),
    path('payment/resume/<int:order_id>/', views.resume_payment, name='resume_payment'),
    path('payment/pay-online/<int:order_id>/', views.pay_online, name='pay_online'),
    path('payment/stripe/webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('staff/seller-document/<int:seller_id>/<str:field>/', views.seller_document, name='seller_document'),
]

# Uploaded files are served by Supabase Storage in production. Without it
# (local development, or a single-server deploy with a persistent disk),
# Django serves the public media folder itself. Private seller documents
# are never exposed here - staff open them via the seller_document view.
if not settings.USE_SUPABASE_STORAGE:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]

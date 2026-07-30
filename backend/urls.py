from django.contrib import admin
from django.urls import path
from daraz import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('product/<int:pk>/', views.product_detail, name='product_detail'),
    path('category/<str:category>/', views.category_products, name='category_products'),
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('auth/google/', views.google_auth, name='google_auth'),
]

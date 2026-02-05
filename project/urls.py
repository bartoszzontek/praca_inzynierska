from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib.auth.views import LoginView, LogoutView
from rest_framework.authtoken import views as drf_views
from sensors.views import register, home_view

urlpatterns = [
    # 1. STRONA GŁÓWNA - energy.zipit.pl (Landing Page)
    path('', home_view, name='home'),

    # 2. PANEL ADMINISTRATORA SYSTEMU
    path('admin/', admin.site.urls),

    # 3. AUTENTYKACJA
    path('login/', LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),
    path('register/', register, name='register'),
    path('api-token-auth/', drf_views.obtain_auth_token, name='api-token-auth'),

    # 4. DASHBOARD I API APLIKACJI
    # energy.zipit.pl/dashboard/ -> pokaże widok dashboard
    # energy.zipit.pl/dashboard/v1/ -> pokaże listę API
    path('dashboard/', include('sensors.urls')),
]

# OBSŁUGA PLIKÓW STATYCZNYCH
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += staticfiles_urlpatterns()
else:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
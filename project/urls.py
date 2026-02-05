from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib.auth.views import LoginView, LogoutView
from rest_framework.authtoken import views as drf_views
from sensors.views import register, home_view  # Zmieniliśmy home_redirect na home_view

urlpatterns = [
    # 1. STRONA GŁÓWNA (Landing Page)
    # To musi być jako pierwsze dla pustego adresu, żeby nie włączało się API
    path('', home_view, name='home'),

    # 2. PANEL ADMINISTRATORA
    path('admin/', admin.site.urls),

    # 3. API I TOKENY
    path('api/', include('sensors.urls')),
    path('api-token-auth/', drf_views.obtain_auth_token, name='api-token-auth'),

    # 4. AUTENTYKACJA (Systemowa Django)
    path('login/', LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', LogoutView.as_view(next_page='/'), name='logout'),
    path('register/', register, name='register'),

    # 5. DASHBOARD I WIDOKI UŻYTKOWNIKA (Jeśli masz je w sensors.urls)
    path('dashboard/', include('sensors.urls')),
]

# OBSŁUGA PLIKÓW STATYCZNYCH (Ważne dla CSS)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += staticfiles_urlpatterns()
else:
    # W trybie produkcji (DEBUG=False) wymuszamy obsługę statyk, jeśli nie używasz Nginxa
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
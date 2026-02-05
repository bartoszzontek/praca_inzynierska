from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib.auth.views import LoginView, LogoutView
from rest_framework.authtoken import views as drf_views
from sensors.views import register, home_redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api-token-auth/', drf_views.obtain_auth_token, name='api-token-auth'),
    path('api/', include('sensors.urls')),
    path('', include('sensors.urls')),
    path('login/', LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', LogoutView.as_view(next_page='/login/'), name='logout'),
    path('register/', register, name='register'),
    path('', home_redirect, name='home'),
]


if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += staticfiles_urlpatterns()
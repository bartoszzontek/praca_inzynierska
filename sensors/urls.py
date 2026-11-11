from django.urls import path, include
from django.views.generic import RedirectView
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'admin/houses', views.AdminHouseViewSet, basename='admin-houses')
router.register(r'admin/sensors', views.AdminSensorViewSet, basename='admin-sensors')
router.register(r'user/houses', views.UserHouseViewSet, basename='user-houses')
router.register(r'user/sensors', views.UserSensorViewSet, basename='user-sensors')
router.register(r'user/alerts', views.AlertViewSet, basename='user-alerts')
router.register(r'user/settings', views.UserSettingsViewSet, basename='user-settings')

api_urlpatterns = [
    path('v1/', include(router.urls)),
    path('user/me/', views.user_me_view, name='user-me'),
    path('user/live/', views.dashboard_live, name='dashboard-live'),
    path('user/sensor/<int:sensor_id>/data/', views.sensor_data_view, name='sensor-data'),
    path('user/sensor/<int:sensor_id>/live/', views.live_data_view, name='live-data'),
    # odbiór pomiarów z modułu ESP8266 (uwierzytelnianie kluczem API czujnika)
    path('admin/sensor/readings/', views.receive_sensor_readings, name='receive-sensor-readings'),
]

html_urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('house/<int:house_id>/', views.house_view, name='house'),
    path('house/<int:house_id>/settings/', views.house_settings, name='house_settings'),
    path('sensor/<int:sensor_id>/', views.sensor_detail, name='sensor_detail'),
    path('sensor/<int:sensor_id>/settings/', views.sensor_settings, name='sensor_settings'),
    path('sensor/<int:sensor_id>/export.csv', views.sensor_export_csv, name='sensor_export'),
    path('account/', views.account_view, name='account'),
    path('account/theme/', views.toggle_theme, name='toggle_theme'),
    path('alerts/', views.alerts_view, name='alerts'),
    path('alerts/mark-all-read/', views.alerts_mark_all_read, name='alerts_mark_all_read'),
    path('alerts/<int:alert_id>/<str:action>/', views.alert_action, name='alert_action'),

    path('admin-panel/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/sensors/', views.admin_sensors, name='admin_sensors'),
    path('admin-panel/assign/', views.assign_house_view, name='assign_house'),

    # stare adresy – przekierowania
    path('settings/', RedirectView.as_view(pattern_name='account'), name='settings'),
    path('profile/', RedirectView.as_view(pattern_name='account'), name='profile'),
    path('comparison/<int:house_id>/', RedirectView.as_view(pattern_name='house'), name='comparison'),
]

urlpatterns = html_urlpatterns + api_urlpatterns

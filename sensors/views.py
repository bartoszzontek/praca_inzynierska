import logging
from datetime import timedelta
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Avg, Max, Min, Count
from django.db import models
from django.utils.http import url_has_allowed_host_and_scheme

MONTHS_PL = ['', 'Styczeń', 'Luty', 'Marzec', 'Kwiecień', 'Maj', 'Czerwiec',
             'Lipiec', 'Sierpień', 'Wrzesień', 'Październik', 'Listopad', 'Grudzień']
MONTHS_PL_SHORT = ['', 'sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru']

try:
    from .forms import CustomUserCreationForm
except ImportError:
    from django.contrib.auth.forms import UserCreationForm as CustomUserCreationForm


from rest_framework import viewsets, status, generics, serializers
from rest_framework.response import Response
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    action
)
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from .models import House, Sensor, SensorData, Alert, UserSettings, ActivityLog
from .serializers import (
    HouseSerializer,
    SensorSerializer,
    SensorDataSerializer,
    SensorReadingSerializer,
    AlertSerializer,
    UserSettingsSerializer,
    UserSerializer
)
from .utils import (
    calculate_reactive_power,
    check_alerts,
    log_activity,
    get_comparison_data,
    predict_monthly_cost,
    calculate_energy_for_period
)

logger = logging.getLogger(__name__)


# REJESTRACJA I PROFILE

def register(request):
    """Widok rejestracji nowego użytkownika"""
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password2') 

            UserSettings.objects.create(user=user)

            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)

            log_activity(
                user=user,
                action='create',
                model_name='User',
                object_id=user.id,
                description=f"Utworzono nowe konto: {username}",
                request=request
            )

            messages.success(request, f'Konto utworzone pomyślnie! Witaj {username}!')
            return redirect('dashboard') # Przekierowanie na stary dashboard
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    if field != 'password2': 
                        messages.error(request, f"{error}")
    else:
        form = CustomUserCreationForm()
    
    return render(request, 'login.html', {'register_form': form})

@login_required
def profile(request):
    houses = House.objects.filter(user=request.user).prefetch_related('sensors')
    total_sensors = sum(house.sensors.count() for house in houses)
    settings, created = UserSettings.objects.get_or_create(user=request.user)
    total_alerts = Alert.objects.filter(house__user=request.user).count()
    unread_alerts = Alert.objects.filter(house__user=request.user, is_read=False).count()
    context = {'houses': houses, 'total_sensors': total_sensors, 'user_settings': settings, 'total_alerts': total_alerts, 'unread_alerts': unread_alerts}
    return render(request, 'profile.html', context)

@login_required
def settings_view(request):
    user_settings, created = UserSettings.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        theme = request.POST.get('theme', 'dark')
        user_settings.theme = theme if theme in ('dark', 'light') else 'dark'
        user_settings.email_alerts = request.POST.get('email_alerts') == 'on'
        frequency = request.POST.get('alert_frequency', 'immediate')
        user_settings.alert_frequency = frequency if frequency in ('immediate', 'hourly', 'daily') else 'immediate'
        try:
            interval = int(request.POST.get('live_refresh_interval', 5))
        except (TypeError, ValueError):
            interval = 5
        user_settings.live_refresh_interval = min(60, max(1, interval))
        user_settings.show_predictions = request.POST.get('show_predictions') == 'on'
        monthly_goal = (request.POST.get('monthly_goal_kwh') or '').replace(',', '.').strip()
        try:
            goal = float(monthly_goal) if monthly_goal else None
        except ValueError:
            goal = None
        user_settings.monthly_goal_kwh = goal if goal and goal > 0 else None
        user_settings.save()
        log_activity(user=request.user, action='update', model_name='UserSettings', object_id=user_settings.id, description="Zaktualizowano ustawienia użytkownika", request=request)
        messages.success(request, 'Ustawienia zapisane!')
        return redirect('settings')
    context = {'user_settings': user_settings}
    return render(request, 'settings.html', context)


@login_required
def toggle_theme(request):
    """Szybkie przełączenie motywu jasny / ciemny z paska nawigacji."""
    if request.method == 'POST':
        user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
        user_settings.theme = 'light' if user_settings.theme == 'dark' else 'dark'
        user_settings.save(update_fields=['theme', 'updated_at'])
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = 'dashboard'
    return redirect(next_url)


#API ENDPOINTS

@csrf_exempt
@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAdminUser])
def receive_sensor_readings(request):
    serializer = SensorReadingSerializer(data=request.data, many=True)
    if serializer.is_valid():
        for reading in serializer.validated_data:
            try: sensor = Sensor.objects.get(sensor_id=reading['sensor_id'])
            except Sensor.DoesNotExist:
                logger.warning(f"Sensor {reading['sensor_id']} nie istnieje.")
                continue
            reactive_power = calculate_reactive_power(reading['power'], reading['pf'])
            sensor_data = SensorData.objects.create(
                sensor=sensor, timestamp=reading['timestamp'], voltage=reading['voltage'],
                current=reading['current'], power=reading['power'], energy=reading['energy'],
                frequency=reading['frequency'], pf=reading['pf'], reactive_power=reactive_power
            )
            check_alerts(sensor, sensor_data)
        return Response({"status": "ok"}, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_me_view(request):
    """
    Zwraca dane zalogowanego użytkownika wraz z jego ustawieniami.
    """
    try:
        # get_or_create zapewnia, że każdy user ma ustawienia
        UserSettings.objects.get_or_create(user=request.user)
        user = User.objects.prefetch_related('settings').get(pk=request.user.pk)
        serializer = UserSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ========== VIEWSETS ==========

class AdminHouseViewSet(viewsets.ModelViewSet):
    # (bez zmian)
    queryset = House.objects.all()
    serializer_class = HouseSerializer
    permission_classes = [IsAdminUser]


class AdminSensorViewSet(viewsets.ModelViewSet):

    queryset = Sensor.objects.all()
    serializer_class = SensorSerializer
    permission_classes = [IsAdminUser]

class UserHouseViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HouseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Prefetch_related, aby pobrać czujniki jednym zapytaniem
        return House.objects.filter(user=self.request.user).prefetch_related('sensors')


    @action(detail=True, methods=['get'], url_path='statistics')
    def statistics(self, request, pk=None):
        house = self.get_object()
        
        day_comparison = get_comparison_data(house, 'day')
        week_comparison = get_comparison_data(house, 'week')
        month_comparison = get_comparison_data(house, 'month')
        prediction = predict_monthly_cost(house)
        
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        sensor_rankings = []
        for sensor in house.sensors.all():
            total_kwh = calculate_energy_for_period(house, start_of_month, now, sensor_id=sensor.id)
            sensor_rankings.append({
                'sensor_id': sensor.id,
                'sensor_name': sensor.name,
                'location': sensor.location,
                'kwh': round(total_kwh, 2),
                'cost': round(total_kwh * house.price_per_kwh, 2)
            })
        sensor_rankings.sort(key=lambda x: x['kwh'], reverse=True)

        return Response({
            'day_comparison': day_comparison,
            'week_comparison': week_comparison,
            'month_comparison': month_comparison,
            'prediction': prediction,
            'sensor_rankings': sensor_rankings,
        }, status=status.HTTP_200_OK)


class UserSensorViewSet(viewsets.ModelViewSet): # Zmieniono na ModelViewSet
    serializer_class = SensorSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        houses = House.objects.filter(user=self.request.user)
        return Sensor.objects.filter(house__in=houses)

    # Pozwalamy użytkownikowi na aktualizację WŁASNYCH czujników
    def update(self, request, *args, **kwargs):
        sensor = self.get_object()
        if sensor.house.user != request.user:
            return Response({"error": "Brak uprawnień"}, status=status.HTTP_403_FORBIDDEN)
        
        # Ograniczamy pola, które user może edytować
        editable_fields = [
            'name', 'description', 'location', 'icon', 'color', 
            'power_threshold', 'current_max_threshold', 
            'voltage_min_threshold', 'voltage_max_threshold', 
            'offline_threshold_seconds'
        ]
        
        # Tworzymy częściowy serializer
        serializer = self.get_serializer(sensor, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        
        # Walidacja, czy user nie próbuje zmienić pól, których nie powinien
        for field in request.data:
            if field not in editable_fields:
                return Response({"error": f"Nie możesz edytować pola '{field}'."}, status=status.HTTP_400_BAD_REQUEST)
                
        self.perform_update(serializer)
        return Response(serializer.data)


class AlertViewSet(viewsets.ModelViewSet):
    serializer_class = AlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Filtrowanie GET (dla panelu alertów)
        alerts = Alert.objects.filter(house__user=self.request.user).order_by('-created_at')
        
        filter_type = self.request.query_params.get('type')
        filter_severity = self.request.query_params.get('severity')
        filter_status = self.request.query_params.get('status')

        if filter_type:
            alerts = alerts.filter(alert_type=filter_type)
        if filter_severity:
            alerts = alerts.filter(severity=filter_severity)
        if filter_status == 'unread':
            alerts = alerts.filter(is_read=False)
        elif filter_status == 'resolved':
            alerts = alerts.filter(is_resolved=True)
        elif filter_status == 'active': # Aktywne = nierozwiązane
            alerts = alerts.filter(is_resolved=False)
            
        return alerts
    
    # Dodajemy 'perform_create', aby automatycznie ustawić dom
    def perform_create(self, serializer):
        house = serializer.validated_data.get('house')
        if house.user != self.request.user:
             raise serializers.ValidationError("Nie możesz dodać alertu do nie swojego domu.")
        serializer.save()

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        alert = self.get_object()
        alert.is_read = True
        alert.save()
        return Response({'status': 'marked as read'})

    @action(detail=True, methods=['post'])
    def mark_resolved(self, request, pk=None):
        alert = self.get_object()
        alert.is_resolved = True
        alert.save()
        return Response({'status': 'marked as resolved'})


class UserSettingsViewSet(viewsets.ModelViewSet):
    serializer_class = UserSettingsSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return UserSettings.objects.filter(user=self.request.user)

    def get_object(self):
        obj, created = UserSettings.objects.get_or_create(user=self.request.user)
        return obj
    

    def list(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)
        

    def destroy(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        

    def create(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sensor_data_view(request, sensor_id):
    sensor = get_object_or_404(Sensor, id=sensor_id)
    if sensor.house.user != request.user:
        return Response({'error': 'Brak dostępu'}, status=status.HTTP_403_FORBIDDEN)
    
    # TODO: Dodać filtrowanie po zakresie dat z query params
    start_date = timezone.now() - timedelta(days=1)
    qs = SensorData.objects.filter(
        sensor=sensor, 
        timestamp__gte=start_date
    ).order_by('timestamp')
    
    return Response(SensorDataSerializer(qs, many=True).data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_data_view(request, sensor_id):
    sensor = get_object_or_404(Sensor, id=sensor_id)
    if sensor.house.user != request.user:
        return Response({'error': 'Brak dostępu'}, status=status.HTTP_403_FORBIDDEN)
    

    price_per_kwh = sensor.house.price_per_kwh

    latest = sensor.data.order_by('-timestamp').first()
    if not latest:
        return Response({'power': 0, 'voltage': 0, 'current': 0, 'pf': 0, 'is_online': sensor.is_online, 'cost_per_hour': 0, 'timestamp': None})

    cost_per_hour = (latest.power / 1000.0) * price_per_kwh if latest.power else 0

    return Response({
        'timestamp': latest.timestamp,
        'power': latest.power,
        'voltage': latest.voltage,
        'current': latest.current,
        'pf': latest.pf,
        'is_online': sensor.is_online,
        'cost_per_hour': cost_per_hour
    })


# ========== STARE WIDOKI HTML (ZOSTAJĄ) ==========
@login_required
def dashboard(request):
    houses = House.objects.filter(user=request.user).prefetch_related('sensors')
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    all_sensors = Sensor.objects.filter(house__user=request.user)
    total_sensors = all_sensors.count()
    online_sensors = sum(1 for sensor in all_sensors if sensor.is_online)
    latest_data = SensorData.objects.filter(sensor__house__user=request.user).order_by('-timestamp').first()
    last_reading_time = latest_data.timestamp if latest_data else None

    houses_with_costs = []
    total_month_kwh = 0
    for house in houses:
        monthly_kwh = calculate_energy_for_period(house, start_of_month, now)
        total_month_kwh += monthly_kwh
        current_power = 0
        sensor_count = 0
        for sensor in house.sensors.all():
            last = sensor.data.order_by('-timestamp').first()
            if last:
                sensor_count += 1
                if last.power and (now - last.timestamp) < timedelta(minutes=5):
                    current_power += float(last.power)
        houses_with_costs.append({
            'house': house, 'monthly_kwh': round(monthly_kwh, 2),
            'monthly_cost': round(monthly_kwh * house.price_per_kwh, 2),
            'current_power': round(current_power, 0), 'sensor_count': sensor_count,
            'prediction': predict_monthly_cost(house) if user_settings.show_predictions else None,
        })

    goal = None
    if user_settings.monthly_goal_kwh:
        percent = total_month_kwh / user_settings.monthly_goal_kwh * 100
        goal = {
            'target': user_settings.monthly_goal_kwh, 'current': round(total_month_kwh, 2),
            'percent': round(percent, 1), 'bar': min(100, round(percent, 1)),
            'exceeded': percent > 100,
        }

    unread_alerts = Alert.objects.filter(house__user=request.user, is_read=False).order_by('-created_at')[:5]
    context = {
        'houses': houses, 'houses_with_costs': houses_with_costs,
        'current_month': f"{MONTHS_PL[now.month]} {now.year}",
        'unread_alerts': unread_alerts, 'total_sensors': total_sensors, 'online_sensors': online_sensors,
        'last_reading_time': last_reading_time, 'goal': goal,
        'show_predictions': user_settings.show_predictions,
    }
    return render(request, 'dashboard.html', context)

@login_required
def sensor_detail(request, sensor_id):
    sensor = get_object_or_404(Sensor, id=sensor_id)
    if sensor.house.user != request.user: return HttpResponseForbidden("Brak dostępu")
    now = timezone.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    price_per_kwh = sensor.house.price_per_kwh
    daily_kwh = calculate_energy_for_period(sensor.house, start_of_day, now, sensor_id=sensor.id)
    daily_cost = daily_kwh * price_per_kwh
    monthly_kwh = calculate_energy_for_period(sensor.house, start_of_month, now, sensor_id=sensor.id)
    monthly_cost = monthly_kwh * price_per_kwh
    last_10_readings = SensorData.objects.filter(sensor=sensor).order_by('-timestamp')[:10]
    recent_readings = SensorData.objects.filter(sensor=sensor).order_by('-timestamp')[:5]
    avg_stats = recent_readings.aggregate(power=Avg('power'), voltage=Avg('voltage'), current=Avg('current'), pf=Avg('pf'))
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    latest = last_10_readings[0] if last_10_readings else None
    cost_per_hour = (latest.power / 1000.0) * price_per_kwh if latest and latest.power else 0
    context = {
        'sensor': sensor, 'avg_stats': avg_stats, 'cost_per_hour': cost_per_hour, 'daily_kwh': daily_kwh, 'daily_cost': daily_cost,
        'monthly_kwh': monthly_kwh, 'monthly_cost': monthly_cost, 'last_10_readings': last_10_readings,
        'refresh_interval': user_settings.live_refresh_interval,
    }
    return render(request, 'sensor_detail.html', context)

@login_required
def create_alert(request):
    from .forms import AlertForm 
    if request.method == 'POST':
        form = AlertForm(request.POST, user=request.user)
        if form.is_valid():
            alert = form.save(commit=False)
            alert.is_resolved = False 
            alert.save()
            log_activity(user=request.user, action='create', model_name='Alert', object_id=alert.id, description=f"Ręcznie utworzono alert: {alert.message}", request=request)
            messages.success(request, 'Pomyślnie utworzono nowy alert.')
            return redirect('alerts')
        else:
            messages.error(request, 'Formularz zawiera błędy. Popraw je.')
    else:
        form = AlertForm(user=request.user)
    context = {'form': form}
    return render(request, 'alert_create.html', context)

@login_required
def alerts_view(request):
    alerts = Alert.objects.filter(house__user=request.user).order_by('-created_at')
    filter_type = request.GET.get('type')
    filter_severity = request.GET.get('severity')
    filter_status = request.GET.get('status')
    if filter_type: alerts = alerts.filter(alert_type=filter_type)
    if filter_severity: alerts = alerts.filter(severity=filter_severity)
    if filter_status == 'unread': alerts = alerts.filter(is_read=False)
    elif filter_status == 'resolved': alerts = alerts.filter(is_resolved=True)
    elif filter_status == 'active': alerts = alerts.filter(is_resolved=False)
    context = {'alerts': alerts[:50], 'filter_type': filter_type, 'filter_severity': filter_severity, 'filter_status': filter_status}
    return render(request, 'alerts.html', context)

@login_required
def alert_action(request, alert_id, action):
    """Oznaczenie alertu jako przeczytany / rozwiązany z poziomu strony alertów."""
    if request.method != 'POST':
        return redirect('alerts')
    alert = get_object_or_404(Alert, id=alert_id, house__user=request.user)
    if action == 'read':
        alert.is_read = True
        alert.save(update_fields=['is_read'])
    elif action == 'resolve':
        alert.is_read = True
        alert.is_resolved = True
        alert.save(update_fields=['is_read', 'is_resolved'])
    elif action == 'delete':
        alert.delete()
        messages.success(request, 'Alert usunięty.')
    next_url = request.POST.get('next', '')
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect('alerts')


@login_required
def alerts_mark_all_read(request):
    if request.method == 'POST':
        count = Alert.objects.filter(house__user=request.user, is_read=False).update(is_read=True)
        messages.success(request, f'Oznaczono jako przeczytane: {count}')
    return redirect('alerts')


@login_required
def comparison_view(request, house_id):
    house = get_object_or_404(House, id=house_id, user=request.user)
    day_comparison = get_comparison_data(house, 'day')
    week_comparison = get_comparison_data(house, 'week')
    month_comparison = get_comparison_data(house, 'month')
    prediction = predict_monthly_cost(house)
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sensor_rankings = []
    for sensor in house.sensors.all():
        total_kwh = calculate_energy_for_period(house, start_of_month, now, sensor_id=sensor.id)
        sensor_rankings.append({'sensor': sensor, 'kwh': round(total_kwh, 2), 'cost': round(total_kwh * house.price_per_kwh, 2)})
    sensor_rankings.sort(key=lambda x: x['kwh'], reverse=True)
    monthly_history = []
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for i in range(12):
        if i == 0:
            month_start = current_month_start
            month_end = now
        else:
            month_end = current_month_start
            month_start = (month_end - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            current_month_start = month_start
        month_kwh = calculate_energy_for_period(house, month_start, month_end)
        monthly_history.append({'month': f"{MONTHS_PL_SHORT[month_start.month]} {month_start.year}", 'kwh': round(month_kwh, 2), 'cost': round(month_kwh * house.price_per_kwh, 2)})
    monthly_history.reverse()
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    context = {
        'house': house, 'day_comparison': day_comparison, 'week_comparison': week_comparison,
        'month_comparison': month_comparison, 'prediction': prediction,
        'show_predictions': user_settings.show_predictions,
        'sensor_rankings': sensor_rankings, 'monthly_history': monthly_history,
    }
    return render(request, 'comparison.html', context)

@login_required
def admin_dashboard(request):
    if not request.user.is_staff: return HttpResponseForbidden("Brak dostępu")
    total_users = User.objects.count()
    total_houses = House.objects.count()
    total_sensors = Sensor.objects.count()
    online_sensors = sum(1 for s in Sensor.objects.all() if s.is_online)
    unread_alerts = Alert.objects.filter(is_read=False).count()
    critical_alerts = Alert.objects.filter(severity='critical', is_resolved=False).count()
    recent_activity = ActivityLog.objects.all().order_by('-created_at')[:20]
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    user_consumptions = []
    for user in User.objects.filter(is_active=True):
        houses = House.objects.filter(user=user)
        total_kwh = 0
        for house in houses:
            total_kwh += calculate_energy_for_period(house, start_of_month, now)
        if total_kwh > 0:
            user_consumptions.append({'user': user, 'kwh': round(total_kwh, 2), 'houses': houses.count()})
    user_consumptions.sort(key=lambda x: x['kwh'], reverse=True)
    context = {
        'total_users': total_users, 'total_houses': total_houses, 'total_sensors': total_sensors,
        'online_sensors': online_sensors, 'offline_sensors': total_sensors - online_sensors,
        'unread_alerts': unread_alerts, 'critical_alerts': critical_alerts,
        'recent_activity': recent_activity, 'top_users': user_consumptions[:10],
    }
    return render(request, 'admin_dashboard.html', context)

@login_required
def admin_sensor_list_view(request):
    if not request.user.is_staff: return HttpResponseForbidden("Brak dostępu")
    from django.db import models 
    all_sensors = Sensor.objects.all().select_related('house').prefetch_related(
        models.Prefetch('data', queryset=SensorData.objects.order_by('-timestamp'), to_attr='last_reading_list')
    ).order_by('house__name', 'name')
    for sensor in all_sensors:
        sensor.data.first = sensor.last_reading_list[0] if sensor.last_reading_list else None
    context = {'all_sensors': all_sensors}
    return render(request, 'admin_sensor_list.html', context)

@login_required
def assign_house_view(request):
    if not request.user.is_staff: return HttpResponseForbidden("Brak dostępu")
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        house_name = (request.POST.get('house_name') or '').strip()
        address = (request.POST.get('address') or '').strip()
        try:
            price_per_kwh = float((request.POST.get('price_per_kwh') or '0.80').replace(',', '.'))
        except ValueError:
            price_per_kwh = -1
        if not house_name or price_per_kwh < 0:
            messages.error(request, 'Podaj nazwę domu i poprawną cenę za kWh.')
            return redirect('assign_house')
        user = get_object_or_404(User, id=user_id)
        house = House.objects.create(user=user, name=house_name, address=address, price_per_kwh=price_per_kwh)
        log_activity(user=request.user, action='assign', model_name='House', object_id=house.id, description=f"Przypisano dom '{house_name}' do użytkownika {user.username}", request=request)
        messages.success(request, f"Dom '{house_name}' został przypisany do {user.username}")
        return redirect('assign_house')
    users = User.objects.filter(is_active=True).order_by('username')
    all_houses = House.objects.all().select_related('user').order_by('-created_at')[:50]
    context = {'users': users, 'all_houses': all_houses}
    return render(request, 'admin_assign.html', context)

def home_redirect(request):
    if request.user.is_authenticated:
        return redirect('dashboard')  # Jeśli zalogowany, idź do dashboardu
    return redirect('login')         # Jeśli nie, idź do logowania

def home_view(request):
    # Jeśli użytkownik jest już zalogowany, opcjonalnie możesz go od razu rzucić do dashboardu
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'home.html')

import csv
import logging
from datetime import timedelta, datetime

from django.contrib import messages
from django.contrib.auth import login, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Avg
from django.db.models.functions import TruncHour
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from rest_framework import viewsets, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, action
from rest_framework.permissions import IsAdminUser, IsAuthenticated, AllowAny
from rest_framework.response import Response

from .forms import CustomUserCreationForm, SensorSettingsForm, HouseSettingsForm, AdminSensorForm
from .models import House, Sensor, SensorData, Alert, UserSettings, ActivityLog, generate_sensor_token
from .serializers import (
    HouseSerializer, SensorSerializer, SensorReadingSerializer,
    AlertSerializer, UserSettingsSerializer, UserSerializer,
)
from .utils import (
    calculate_reactive_power, check_alerts, log_activity, get_comparison_data,
    predict_monthly_cost, calculate_energy_for_period, energy_by_day,
)

logger = logging.getLogger(__name__)

MONTHS_PL = ['', 'Styczeń', 'Luty', 'Marzec', 'Kwiecień', 'Maj', 'Czerwiec',
             'Lipiec', 'Sierpień', 'Wrzesień', 'Październik', 'Listopad', 'Grudzień']
MONTHS_PL_SHORT = ['', 'sty', 'lut', 'mar', 'kwi', 'maj', 'cze', 'lip', 'sie', 'wrz', 'paź', 'lis', 'gru']


def _safe_next(request, default):
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return default


def _period_starts(now):
    """Początek bieżącej doby i miesiąca w strefie czasowej aplikacji."""
    local = timezone.localtime(now)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


def _sensor_status(sensor, now):
    """Ostatni pomiar, status online i bieżąca moc czujnika."""
    last = sensor.data.order_by('-timestamp').only('timestamp', 'power').first()
    online = bool(last) and (now - last.timestamp) < timedelta(seconds=sensor.offline_threshold_seconds)
    return {
        'sensor': sensor,
        'last': last,
        'online': online,
        'power': round(last.power or 0) if (last and online) else None,
    }


def _house_power_24h(house, now):
    """Średnia moc domu w kolejnych godzinach ostatniej doby (dane do mini-wykresu)."""
    rows = (SensorData.objects
            .filter(sensor__house=house, timestamp__gte=now - timedelta(hours=24))
            .annotate(hour=TruncHour('timestamp'))
            .values('hour', 'sensor_id')
            .annotate(avg_power=Avg('power')))
    per_hour = {}
    for row in rows:
        per_hour[row['hour']] = per_hour.get(row['hour'], 0) + (row['avg_power'] or 0)
    return [{'t': timezone.localtime(h).strftime('%H:%M'), 'p': round(p)} for h, p in sorted(per_hour.items())]


# ========== KONTO ==========

def register(request):
    """Rejestracja nowego użytkownika"""
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserSettings.objects.get_or_create(user=user)
            user = authenticate(username=form.cleaned_data['username'], password=form.cleaned_data['password2'])
            if user is not None:
                login(request, user)
                log_activity(user=user, action='create', model_name='User', object_id=user.id,
                             description=f"Utworzono nowe konto: {user.username}", request=request)
            messages.success(request, f'Konto utworzone. Witaj, {form.cleaned_data["username"]}!')
            return redirect('dashboard')
    else:
        form = CustomUserCreationForm()
    return render(request, 'login.html', {'register_form': form})


@login_required
def account_view(request):
    """Konto: dane użytkownika, preferencje i lista domów."""
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        if email and email != request.user.email:
            if User.objects.filter(email=email).exclude(pk=request.user.pk).exists():
                messages.error(request, 'Ten adres email jest już używany.')
                return redirect('account')
            request.user.email = email
            request.user.save(update_fields=['email'])

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
        goal_raw = (request.POST.get('monthly_goal_kwh') or '').replace(',', '.').strip()
        try:
            goal = float(goal_raw) if goal_raw else None
        except ValueError:
            goal = None
        user_settings.monthly_goal_kwh = goal if goal and goal > 0 else None
        user_settings.save()
        log_activity(user=request.user, action='update', model_name='UserSettings', object_id=user_settings.id,
                     description="Zaktualizowano ustawienia konta", request=request)
        messages.success(request, 'Ustawienia zapisane.')
        return redirect('account')

    houses = House.objects.filter(user=request.user).prefetch_related('sensors')
    context = {
        'user_settings': user_settings,
        'houses': houses,
        'total_sensors': sum(h.sensors.count() for h in houses),
        'total_alerts': Alert.objects.filter(house__user=request.user).count(),
    }
    return render(request, 'account.html', context)


@login_required
def toggle_theme(request):
    """Szybkie przełączenie motywu jasny / ciemny z paska nawigacji."""
    if request.method == 'POST':
        user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
        user_settings.theme = 'light' if user_settings.theme == 'dark' else 'dark'
        user_settings.save(update_fields=['theme', 'updated_at'])
    return redirect(_safe_next(request, 'dashboard'))


# ========== API ==========

@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def receive_sensor_readings(request):
    """
    Odbiór pomiarów z modułu pomiarowego.
    Nagłówek: Authorization: Token <klucz API czujnika>.
    Każdy czujnik ma własny klucz i może zapisywać tylko swoje pomiary.
    """
    header = request.META.get('HTTP_AUTHORIZATION', '')
    key = header.split(' ', 1)[1].strip() if header.startswith('Token ') else ''
    sensor = Sensor.objects.select_related('house').filter(api_token=key, is_active=True).first() if key else None
    if sensor is None:
        return Response({'detail': 'Nieprawidłowy klucz API czujnika.'}, status=status.HTTP_401_UNAUTHORIZED)

    data = request.data if isinstance(request.data, list) else [request.data]
    serializer = SensorReadingSerializer(data=data, many=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    if any(reading['sensor_id'] != sensor.sensor_id for reading in serializer.validated_data):
        return Response({'detail': 'Klucz API nie pasuje do identyfikatora czujnika.'},
                        status=status.HTTP_403_FORBIDDEN)

    for reading in serializer.validated_data:
        sensor_data = SensorData.objects.create(
            sensor=sensor, timestamp=reading['timestamp'], voltage=reading['voltage'],
            current=reading['current'], power=reading['power'], energy=reading['energy'],
            frequency=reading['frequency'], pf=reading['pf'],
            reactive_power=calculate_reactive_power(reading['power'], reading['pf']),
        )
        check_alerts(sensor, sensor_data)
    return Response({'status': 'ok', 'saved': len(serializer.validated_data)}, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_me_view(request):
    """Dane zalogowanego użytkownika wraz z ustawieniami."""
    UserSettings.objects.get_or_create(user=request.user)
    user = User.objects.prefetch_related('settings').get(pk=request.user.pk)
    return Response(UserSerializer(user).data)


class AdminHouseViewSet(viewsets.ModelViewSet):
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
        return House.objects.filter(user=self.request.user).prefetch_related('sensors')

    @action(detail=True, methods=['get'], url_path='statistics')
    def statistics(self, request, pk=None):
        house = self.get_object()
        now = timezone.now()
        _, start_of_month = _period_starts(now)
        rankings = []
        for sensor in house.sensors.all():
            kwh = calculate_energy_for_period(house, start_of_month, now, sensor_id=sensor.id)
            rankings.append({'sensor_id': sensor.id, 'sensor_name': sensor.name, 'location': sensor.location,
                             'kwh': round(kwh, 2), 'cost': round(kwh * house.price_per_kwh, 2)})
        rankings.sort(key=lambda x: x['kwh'], reverse=True)
        return Response({
            'day_comparison': get_comparison_data(house, 'day'),
            'week_comparison': get_comparison_data(house, 'week'),
            'month_comparison': get_comparison_data(house, 'month'),
            'prediction': predict_monthly_cost(house),
            'sensor_rankings': rankings,
        })


class UserSensorViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SensorSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Sensor.objects.filter(house__user=self.request.user)


class AlertViewSet(viewsets.ModelViewSet):
    """Alerty użytkownika. Tworzy je system – przez API można je tylko czytać, oznaczać i usuwać."""
    serializer_class = AlertSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        alerts = Alert.objects.filter(house__user=self.request.user).order_by('-created_at')
        params = self.request.query_params
        if params.get('type'):
            alerts = alerts.filter(alert_type=params['type'])
        if params.get('severity'):
            alerts = alerts.filter(severity=params['severity'])
        if params.get('status') == 'unread':
            alerts = alerts.filter(is_read=False)
        elif params.get('status') == 'resolved':
            alerts = alerts.filter(is_resolved=True)
        elif params.get('status') == 'active':
            alerts = alerts.filter(is_resolved=False)
        return alerts

    def create(self, request, *args, **kwargs):
        return Response({'detail': 'Alerty tworzy system na podstawie progów czujników.'},
                        status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        alert = self.get_object()
        alert.is_read = True
        alert.save(update_fields=['is_read'])
        return Response({'status': 'marked as read'})

    @action(detail=True, methods=['post'])
    def mark_resolved(self, request, pk=None):
        alert = self.get_object()
        alert.is_read = alert.is_resolved = True
        alert.save(update_fields=['is_read', 'is_resolved'])
        return Response({'status': 'marked as resolved'})


class UserSettingsViewSet(viewsets.ModelViewSet):
    serializer_class = UserSettingsSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        return UserSettings.objects.filter(user=self.request.user)

    def get_object(self):
        obj, _ = UserSettings.objects.get_or_create(user=self.request.user)
        return obj

    def list(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sensor_data_view(request, sensor_id):
    """Dane do wykresów czujnika. Parametr range: 24h (pomiary), 7d lub 30d (średnie godzinowe)."""
    sensor = get_object_or_404(Sensor, id=sensor_id)
    if sensor.house.user != request.user:
        return Response({'error': 'Brak dostępu'}, status=status.HTTP_403_FORBIDDEN)
    rng = request.GET.get('range', '24h')
    hours = {'24h': 24, '7d': 24 * 7, '30d': 24 * 30}.get(rng, 24)
    qs = SensorData.objects.filter(sensor=sensor, timestamp__gte=timezone.now() - timedelta(hours=hours))
    if hours == 24:
        rows = qs.order_by('timestamp').values_list('timestamp', 'power', 'voltage')
        data = [{'timestamp': ts, 'power': p, 'voltage': v} for ts, p, v in rows]
    else:
        rows = (qs.annotate(hour=TruncHour('timestamp')).values('hour')
                .annotate(power=Avg('power'), voltage=Avg('voltage')).order_by('hour'))
        data = [{'timestamp': r['hour'], 'power': round(r['power'] or 0, 1), 'voltage': round(r['voltage'] or 0, 1)}
                for r in rows]
    return Response({'range': rng, 'aggregated': hours != 24, 'data': data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_data_view(request, sensor_id):
    """Ostatni pomiar czujnika (odświeżanie strony czujnika)."""
    sensor = get_object_or_404(Sensor, id=sensor_id)
    if sensor.house.user != request.user:
        return Response({'error': 'Brak dostępu'}, status=status.HTTP_403_FORBIDDEN)
    latest = sensor.data.order_by('-timestamp').first()
    if not latest:
        return Response({'power': 0, 'voltage': 0, 'current': 0, 'pf': 0, 'is_online': False,
                         'cost_per_hour': 0, 'timestamp': None})
    online = (timezone.now() - latest.timestamp) < timedelta(seconds=sensor.offline_threshold_seconds)
    return Response({
        'timestamp': latest.timestamp, 'power': latest.power, 'voltage': latest.voltage,
        'current': latest.current, 'pf': latest.pf, 'is_online': online,
        'cost_per_hour': (latest.power or 0) / 1000.0 * sensor.house.price_per_kwh,
    })


@login_required
def dashboard_live(request):
    """Bieżąca moc i status wszystkich czujników użytkownika (odświeżanie dashboardu w tle)."""
    now = timezone.now()
    sensors = []
    for sensor in Sensor.objects.filter(house__user=request.user):
        st = _sensor_status(sensor, now)
        sensors.append({'id': sensor.id, 'house': sensor.house_id, 'online': st['online'], 'power': st['power']})
    return JsonResponse({
        'total_power': sum(s['power'] or 0 for s in sensors),
        'online': sum(1 for s in sensors if s['online']),
        'count': len(sensors),
        'sensors': sensors,
    })


# ========== STRONY APLIKACJI ==========

@login_required
def dashboard(request):
    now = timezone.now()
    start_of_day, start_of_month = _period_starts(now)
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    houses = House.objects.filter(user=request.user).prefetch_related('sensors')

    cards = []
    totals = {'power': 0, 'today_kwh': 0, 'today_cost': 0, 'month_kwh': 0, 'month_cost': 0, 'online': 0, 'sensors': 0}
    for house in houses:
        statuses = [_sensor_status(s, now) for s in house.sensors.all()]
        today_kwh = calculate_energy_for_period(house, start_of_day, now)
        month_kwh = calculate_energy_for_period(house, start_of_month, now)
        power = sum(st['power'] or 0 for st in statuses)
        prediction = predict_monthly_cost(house)
        cards.append({
            'house': house, 'sensors': statuses, 'power': power,
            'today_kwh': today_kwh, 'month_kwh': month_kwh, 'month_cost': month_kwh * house.price_per_kwh,
            'prediction_kwh': prediction['predicted_kwh'], 'prediction_cost': prediction['predicted_cost'],
            'power_24h': _house_power_24h(house, now),
        })
        totals['power'] += power
        totals['today_kwh'] += today_kwh
        totals['today_cost'] += today_kwh * house.price_per_kwh
        totals['month_kwh'] += month_kwh
        totals['month_cost'] += month_kwh * house.price_per_kwh
        totals['online'] += sum(1 for st in statuses if st['online'])
        totals['sensors'] += len(statuses)

    goal = None
    if user_settings.monthly_goal_kwh:
        percent = totals['month_kwh'] / user_settings.monthly_goal_kwh * 100
        goal = {'target': user_settings.monthly_goal_kwh, 'percent': percent,
                'bar': min(100.0, round(percent, 1)), 'exceeded': percent > 100}

    local_now = timezone.localtime(now)
    context = {
        'cards': cards, 'totals': totals, 'goal': goal,
        'current_month': f"{MONTHS_PL[local_now.month]} {local_now.year}",
        'recent_alerts': Alert.objects.filter(house__user=request.user, is_resolved=False)
                                      .select_related('house', 'sensor').order_by('-created_at')[:5],
        'charts': {str(c['house'].id): c['power_24h'] for c in cards},
    }
    return render(request, 'dashboard.html', context)


@login_required
def house_view(request, house_id):
    """Statystyki domu: porównania okresów, zużycie dzienne, ranking czujników, historia 12 miesięcy."""
    house = get_object_or_404(House, id=house_id, user=request.user)
    now = timezone.now()
    _, start_of_month = _period_starts(now)
    sensors = list(house.sensors.all())

    rankings = []
    for sensor in sensors:
        kwh = calculate_energy_for_period(house, start_of_month, now, sensor_id=sensor.id)
        rankings.append({'sensor': sensor, 'kwh': kwh, 'cost': kwh * house.price_per_kwh})
    rankings.sort(key=lambda x: x['kwh'], reverse=True)
    total_month = sum(r['kwh'] for r in rankings)
    for r in rankings:
        r['share'] = r['kwh'] / total_month * 100 if total_month else 0

    # historia 12 miesięcy – jeden przebieg po pomiarach zamiast 12 osobnych obliczeń
    local_now = timezone.localtime(now)
    y, m = local_now.year, local_now.month - 11
    while m <= 0:
        y, m = y - 1, m + 12
    first_month = local_now.replace(year=y, month=m, day=1, hour=0, minute=0, second=0, microsecond=0)
    per_month = {}
    for day in energy_by_day(sensors, days=(local_now.date() - first_month.date()).days + 1):
        key = (day['date'].year, day['date'].month)
        per_month[key] = per_month.get(key, 0) + day['kwh']
    history = []
    for _ in range(12):
        kwh = per_month.get((y, m), 0)
        history.append({'label': f"{MONTHS_PL_SHORT[m]} {y}", 'kwh': round(kwh, 2),
                        'cost': round(kwh * house.price_per_kwh, 2)})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    context = {
        'house': house,
        'comparisons': [
            ('Dziś', 'wczoraj', get_comparison_data(house, 'day')),
            ('Ten tydzień', 'poprzedni tydzień', get_comparison_data(house, 'week')),
            ('Ten miesiąc', 'poprzedni miesiąc', get_comparison_data(house, 'month')),
        ],
        'prediction': predict_monthly_cost(house),
        'rankings': rankings,
        'chart_daily': [{'label': d['date'].strftime('%d.%m'), 'kwh': d['kwh']} for d in energy_by_day(sensors, 14)],
        'chart_history': history,
    }
    return render(request, 'house.html', context)


@login_required
def house_settings(request, house_id):
    house = get_object_or_404(House, id=house_id, user=request.user)
    if request.method == 'POST':
        form = HouseSettingsForm(request.POST, instance=house)
        if form.is_valid():
            form.save()
            log_activity(user=request.user, action='update', model_name='House', object_id=house.id,
                         description=f"Zmieniono ustawienia domu '{house.name}'", request=request)
            messages.success(request, 'Ustawienia domu zapisane.')
            return redirect('house', house_id=house.id)
    else:
        form = HouseSettingsForm(instance=house)
    return render(request, 'house_settings.html', {'house': house, 'form': form})


@login_required
def sensor_detail(request, sensor_id):
    sensor = get_object_or_404(Sensor.objects.select_related('house'), id=sensor_id)
    if sensor.house.user != request.user:
        return HttpResponseForbidden("Brak dostępu")
    now = timezone.now()
    start_of_day, start_of_month = _period_starts(now)
    price = sensor.house.price_per_kwh
    daily_kwh = calculate_energy_for_period(sensor.house, start_of_day, now, sensor_id=sensor.id)
    monthly_kwh = calculate_energy_for_period(sensor.house, start_of_month, now, sensor_id=sensor.id)
    last_readings = list(SensorData.objects.filter(sensor=sensor).order_by('-timestamp')[:10])
    latest = last_readings[0] if last_readings else None
    user_settings, _ = UserSettings.objects.get_or_create(user=request.user)
    online = bool(latest) and (now - latest.timestamp) < timedelta(seconds=sensor.offline_threshold_seconds)
    today = timezone.localdate()
    context = {
        'sensor': sensor, 'latest': latest, 'online': online,
        'cost_per_hour': (latest.power or 0) / 1000.0 * price if latest else 0,
        'daily_kwh': daily_kwh, 'daily_cost': daily_kwh * price,
        'monthly_kwh': monthly_kwh, 'monthly_cost': monthly_kwh * price,
        'last_readings': last_readings,
        'refresh_interval': user_settings.live_refresh_interval,
        'chart_daily': [{'label': d['date'].strftime('%d.%m'), 'kwh': d['kwh']} for d in energy_by_day([sensor], 14)],
        'export_from': (today - timedelta(days=6)).isoformat(),
        'export_to': today.isoformat(),
    }
    return render(request, 'sensor_detail.html', context)


@login_required
def sensor_settings(request, sensor_id):
    sensor = get_object_or_404(Sensor.objects.select_related('house'), id=sensor_id, house__user=request.user)
    if request.method == 'POST':
        if request.POST.get('action') == 'regenerate_token':
            sensor.api_token = generate_sensor_token()
            sensor.save(update_fields=['api_token'])
            log_activity(user=request.user, action='update', model_name='Sensor', object_id=sensor.id,
                         description=f"Wygenerowano nowy klucz API czujnika '{sensor.name}'", request=request)
            messages.success(request, 'Wygenerowano nowy klucz API. Wpisz go w konfiguracji modułu pomiarowego.')
            return redirect('sensor_settings', sensor_id=sensor.id)
        form = SensorSettingsForm(request.POST, instance=sensor)
        if form.is_valid():
            form.save()
            log_activity(user=request.user, action='update', model_name='Sensor', object_id=sensor.id,
                         description=f"Zmieniono ustawienia czujnika '{sensor.name}'", request=request)
            messages.success(request, 'Ustawienia czujnika zapisane.')
            return redirect('sensor_detail', sensor_id=sensor.id)
    else:
        form = SensorSettingsForm(instance=sensor)
    return render(request, 'sensor_settings.html', {'sensor': sensor, 'form': form})


@login_required
def sensor_export_csv(request, sensor_id):
    """Eksport pomiarów czujnika do pliku CSV (parametry from i to w formacie RRRR-MM-DD)."""
    sensor = get_object_or_404(Sensor.objects.select_related('house'), id=sensor_id, house__user=request.user)
    today = timezone.localdate()

    def parse(name, default):
        try:
            return datetime.strptime(request.GET.get(name, ''), '%Y-%m-%d').date()
        except ValueError:
            return default

    date_from, date_to = parse('from', today - timedelta(days=6)), parse('to', today)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), datetime.min.time()), tz)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (f'attachment; filename="pomiary_{sensor.sensor_id or sensor.id}'
                                       f'_{date_from}_{date_to}.csv"')
    response.write('﻿')  # BOM – poprawne polskie znaki po otwarciu w Excelu
    writer = csv.writer(response, delimiter=';')
    writer.writerow(['czas', 'napiecie_V', 'prad_A', 'moc_W', 'energia_kWh', 'czestotliwosc_Hz',
                     'wsp_mocy', 'moc_bierna_var'])
    rows = (SensorData.objects.filter(sensor=sensor, timestamp__gte=start, timestamp__lt=end).order_by('timestamp')
            .values_list('timestamp', 'voltage', 'current', 'power', 'energy', 'frequency', 'pf', 'reactive_power'))
    for ts, *values in rows.iterator(chunk_size=5000):
        writer.writerow([timezone.localtime(ts).strftime('%Y-%m-%d %H:%M:%S')] +
                        ['' if v is None else str(v).replace('.', ',') for v in values])
    log_activity(user=request.user, action='update', model_name='Sensor', object_id=sensor.id,
                 description=f"Eksport CSV czujnika '{sensor.name}' ({date_from} – {date_to})", request=request)
    return response


@login_required
def alerts_view(request):
    alerts = Alert.objects.filter(house__user=request.user).select_related('house', 'sensor').order_by('-created_at')
    f_type, f_severity, f_status = request.GET.get('type'), request.GET.get('severity'), request.GET.get('status')
    if f_type:
        alerts = alerts.filter(alert_type=f_type)
    if f_severity:
        alerts = alerts.filter(severity=f_severity)
    if f_status == 'unread':
        alerts = alerts.filter(is_read=False)
    elif f_status == 'resolved':
        alerts = alerts.filter(is_resolved=True)
    elif f_status == 'active':
        alerts = alerts.filter(is_resolved=False)
    context = {
        'alerts': alerts[:100], 'filter_type': f_type, 'filter_severity': f_severity, 'filter_status': f_status,
        'alert_types': [t for t in Alert.ALERT_TYPES if t[0] != 'anomaly'],
    }
    return render(request, 'alerts.html', context)


@login_required
@require_POST
def alert_action(request, alert_id, action):
    """Oznaczenie alertu jako przeczytany / rozwiązany albo jego usunięcie."""
    alert = get_object_or_404(Alert, id=alert_id, house__user=request.user)
    if action == 'read':
        alert.is_read = True
        alert.save(update_fields=['is_read'])
    elif action == 'resolve':
        alert.is_read = alert.is_resolved = True
        alert.save(update_fields=['is_read', 'is_resolved'])
    elif action == 'delete':
        alert.delete()
        messages.success(request, 'Alert usunięty.')
    return redirect(_safe_next(request, 'alerts'))


@login_required
@require_POST
def alerts_mark_all_read(request):
    count = Alert.objects.filter(house__user=request.user, is_read=False).update(is_read=True)
    messages.success(request, f'Oznaczono jako przeczytane: {count}')
    return redirect('alerts')


# ========== PANEL ADMINISTRATORA ==========

def _staff_only(request):
    return None if request.user.is_staff else HttpResponseForbidden("Brak dostępu")


@login_required
def admin_dashboard(request):
    denied = _staff_only(request)
    if denied:
        return denied
    now = timezone.now()
    sensors = list(Sensor.objects.all())
    online = sum(1 for s in sensors if _sensor_status(s, now)['online'])
    context = {
        'total_users': User.objects.count(),
        'total_houses': House.objects.count(),
        'total_sensors': len(sensors),
        'online_sensors': online,
        'offline_sensors': len(sensors) - online,
        'unresolved_alerts': Alert.objects.filter(is_resolved=False).count(),
        'critical_alerts': Alert.objects.filter(severity='critical', is_resolved=False).count(),
        'readings_24h': SensorData.objects.filter(timestamp__gte=now - timedelta(hours=24)).count(),
        'recent_activity': ActivityLog.objects.select_related('user').order_by('-created_at')[:15],
    }
    return render(request, 'admin_dashboard.html', context)


@login_required
def admin_sensors(request):
    """Lista wszystkich czujników i dodawanie nowego czujnika (z kluczem API)."""
    denied = _staff_only(request)
    if denied:
        return denied
    new_sensor = None
    if request.method == 'POST':
        form = AdminSensorForm(request.POST)
        if form.is_valid():
            new_sensor = form.save()
            log_activity(user=request.user, action='create', model_name='Sensor', object_id=new_sensor.id,
                         description=f"Dodano czujnik '{new_sensor.name}' ({new_sensor.sensor_id}) "
                                     f"do domu '{new_sensor.house.name}'", request=request)
            messages.success(request, f"Dodano czujnik {new_sensor.sensor_id}.")
            form = AdminSensorForm()
    else:
        form = AdminSensorForm()
    now = timezone.now()
    rows = [_sensor_status(s, now) for s in Sensor.objects.select_related('house', 'house__user')
            .order_by('house__user__username', 'house__name', 'name')]
    return render(request, 'admin_sensors.html', {'rows': rows, 'form': form, 'new_sensor': new_sensor})


@login_required
def assign_house_view(request):
    denied = _staff_only(request)
    if denied:
        return denied
    if request.method == 'POST':
        house_name = (request.POST.get('house_name') or '').strip()
        address = (request.POST.get('address') or '').strip()
        try:
            price = float((request.POST.get('price_per_kwh') or '0.80').replace(',', '.'))
        except ValueError:
            price = -1
        if not house_name or price < 0:
            messages.error(request, 'Podaj nazwę domu i poprawną cenę za kWh.')
            return redirect('assign_house')
        user = get_object_or_404(User, id=request.POST.get('user_id'))
        house = House.objects.create(user=user, name=house_name, address=address, price_per_kwh=price)
        log_activity(user=request.user, action='assign', model_name='House', object_id=house.id,
                     description=f"Przypisano dom '{house_name}' do użytkownika {user.username}", request=request)
        messages.success(request, f"Dom '{house_name}' został przypisany do {user.username}")
        return redirect('assign_house')
    context = {
        'users': User.objects.filter(is_active=True).order_by('username'),
        'all_houses': House.objects.select_related('user').prefetch_related('sensors').order_by('-created_at')[:50],
    }
    return render(request, 'admin_assign.html', context)


def home_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'home.html')

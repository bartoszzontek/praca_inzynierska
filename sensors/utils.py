import hmac
import hashlib
import math
from datetime import timedelta
from calendar import monthrange

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from .models import Alert, ActivityLog, SensorData, Sensor # Importuj Sensor
import logging

logger = logging.getLogger(__name__)


def sign_data(value, timestamp):
    """Podpisuje dane HMAC"""
    message = f"{value}-{timestamp}".encode('utf-8')
    secret = settings.SENSOR_DATA_SECRET.encode('utf-8')
    signature = hmac.new(secret, message, digestmod=hashlib.sha256).hexdigest()
    return signature


def verify_signature(value, timestamp, signature):
    """Weryfikuje podpis HMAC"""
    expected = sign_data(value, timestamp)
    return hmac.compare_digest(expected, signature)


def calculate_reactive_power(power, pf):
    """
    Oblicza moc bierną Q [VAR]
    """
    if pf is None or pf == 0 or power is None or power == 0:
        return 0.0
    if pf == 1.0:
        return 0.0
        
    try:
        argument = 1 - (pf * pf)
        if argument < 0: # Zabezpieczenie przed błędami float
            argument = 0
            
        S = power / pf  # Moc pozorna [VA]
        Q = S * math.sqrt(argument)
        return round(Q, 2)
    except (ValueError, OverflowError, ZeroDivisionError):
        return 0.0


def check_alerts(sensor, sensor_data):
    """
    Sprawdza alerty czasu rzeczywistego (dla przychodzących danych).
    """
    alerts_created = []
    now = timezone.now()

    # 1. Alert przekroczenia mocy (z progu w modelu)
    if sensor.power_threshold and sensor_data.power and sensor_data.power > sensor.power_threshold:
        if not Alert.objects.filter(
            sensor=sensor,
            alert_type='power_high',
            is_resolved=False, # Szukamy nierozwiązanego
            created_at__gte=now - timedelta(minutes=10)
        ).exists():
            alert = Alert.objects.create(
                house=sensor.house, sensor=sensor,
                alert_type='power_high', severity='warning',
                message=f"Czujnik '{sensor.name}' przekroczył próg mocy!",
                value=sensor_data.power, threshold=sensor.power_threshold
            )
            alerts_created.append(alert)
    
    # 2. Alert anomalii napięcia (z progów w modelu)
    voltage_alert_message = None
    threshold = None
    
    if sensor.voltage_min_threshold and sensor_data.voltage and sensor_data.voltage < sensor.voltage_min_threshold:
        voltage_alert_message = f"Napięcie spadło poniżej progu: {sensor_data.voltage:.1f} V"
        threshold = sensor.voltage_min_threshold
        
    if sensor.voltage_max_threshold and sensor_data.voltage and sensor_data.voltage > sensor.voltage_max_threshold:
        voltage_alert_message = f"Napięcie przekroczyło próg: {sensor_data.voltage:.1f} V"
        threshold = sensor.voltage_max_threshold

    if voltage_alert_message:
        if not Alert.objects.filter(
            sensor=sensor,
            alert_type='voltage_anomaly',
            is_resolved=False, # Szukamy nierozwiązanego
            created_at__gte=now - timedelta(minutes=60) # Rzadziej, co godzinę
        ).exists():
            alert = Alert.objects.create(
                house=sensor.house, sensor=sensor,
                alert_type='voltage_anomaly', severity='critical',
                message=f"Anomalia napięcia na '{sensor.name}': {voltage_alert_message}",
                value=sensor_data.voltage, threshold=threshold
            )
            alerts_created.append(alert)

    #
    #  Alert przekroczenia prądu
    #
    if sensor.current_max_threshold and sensor_data.current and sensor_data.current > sensor.current_max_threshold:
        if not Alert.objects.filter(
            sensor=sensor,
            alert_type='current_high',
            is_resolved=False,
            created_at__gte=now - timedelta(minutes=10) # Co 10 minut
        ).exists():
            alert = Alert.objects.create(
                house=sensor.house, sensor=sensor,
                alert_type='current_high',
                severity='critical', # Tak jak prosiłeś
                message=f"KRYTYCZNE: Czujnik '{sensor.name}' przekroczył próg prądu!",
                value=sensor_data.current,
                threshold=sensor.current_max_threshold
            )
            alerts_created.append(alert)



    # 4. Alert "Czujnik Wrócił Online"
    last_two = sensor.data.order_by('-timestamp')[:2]
    if len(last_two) == 2:
        time_diff = last_two[0].timestamp - last_two[1].timestamp
        
        if time_diff.total_seconds() > sensor.offline_threshold_seconds:
            # Rozwiąż stary alert "offline", jeśli istniał
            Alert.objects.filter(
                sensor=sensor,
                alert_type='sensor_offline',
                is_resolved=False
            ).update(is_resolved=True, is_read=True)

            # Stwórz nowy alert "online"
            if not Alert.objects.filter(
                sensor=sensor,
                alert_type='sensor_online',
                created_at__gte=now - timedelta(minutes=10)
            ).exists():
                alert = Alert.objects.create(
                    house=sensor.house, sensor=sensor,
                    alert_type='sensor_online', severity='info',
                    message=f"Czujnik '{sensor.name}' wznowił wysyłanie danych po przerwie."
                )
                alerts_created.append(alert)


    # 5. Alert limitu miesięcznego
    if sensor.house.monthly_limit_kwh:
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        if not Alert.objects.filter(
            house=sensor.house,
            alert_type='monthly_limit',
            created_at__gte=start_of_month
        ).exists():
            
            monthly_energy = calculate_energy_for_period(sensor.house, start_of_month, now)
            if monthly_energy > sensor.house.monthly_limit_kwh:
                alert = Alert.objects.create(
                    house=sensor.house, sensor=None,
                    alert_type='monthly_limit', severity='critical',
                    message=f"Dom '{sensor.house.name}' przekroczył miesięczny limit zużycia!",
                    value=monthly_energy, threshold=sensor.house.monthly_limit_kwh
                )
                alerts_created.append(alert)
    
    if alerts_created:
        send_alert_email(alerts_created)

    return alerts_created


def _alert_recipient(house):
    return house.alert_email or house.user.email


def _user_settings(user):
    from .models import UserSettings
    settings_obj, _ = UserSettings.objects.get_or_create(user=user)
    return settings_obj


def _send_alert_message(user, email, alerts, subject):
    """Wysyła jeden email z listą alertów i oznacza je jako wysłane."""
    lines = [f"Witaj {user.username},", "", "Nowe alerty w systemie Energy Monitor:", ""]
    for alert in alerts:
        when = timezone.localtime(alert.created_at).strftime('%Y-%m-%d %H:%M')
        lines.append(f"• [{when}] {alert.house.name} – {alert.get_severity_display()}: {alert.message}")
        if alert.value is not None and alert.threshold is not None:
            lines.append(f"  Wartość: {alert.value:.1f}, próg: {alert.threshold:.1f}")
    lines.extend(["", "Szczegóły: https://energymonitor.fun/dashboard/alerts/", "", "---", "Energy Monitor"])
    try:
        send_mail(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        if getattr(settings, 'EMAIL_HOST', ''):
            # bez serwera SMTP wiadomość trafia tylko do logu – nie oznaczamy jej jako wysłanej
            Alert.objects.filter(id__in=[a.id for a in alerts]).update(email_sent=True)
        return True
    except Exception as e:
        logger.error(f"Błąd wysyłania emaila do {email}: {e}")
        return False


def send_alert_email(alerts):
    """
    Wysyła email z alertami od razu – tylko gdy użytkownik ma włączone
    powiadomienia email z częstotliwością "natychmiast". Przy częstotliwości
    godzinowej lub dziennej alerty czekają na podsumowanie (send_alert_digests).
    """
    if not alerts:
        return
    house = alerts[0].house
    user_settings = _user_settings(house.user)
    if not user_settings.email_alerts or user_settings.alert_frequency != 'immediate':
        return
    email = _alert_recipient(house)
    if not email:
        return
    _send_alert_message(house.user, email, alerts, f"Alerty – {house.name}")


def send_alert_digests():
    """
    Wysyła zbiorcze podsumowania alertów dla użytkowników z częstotliwością
    "co godzinę" lub "raz dziennie". Podsumowanie wychodzi, gdy najstarszy
    niewysłany alert czeka dłużej niż wybrany okres. Zwraca liczbę emaili.
    """
    from .models import UserSettings
    if not getattr(settings, 'EMAIL_HOST', ''):
        return 0  # brak skonfigurowanego serwera SMTP
    sent = 0
    now = timezone.now()
    periods = {'hourly': timedelta(hours=1), 'daily': timedelta(days=1)}
    for user_settings in UserSettings.objects.filter(email_alerts=True, alert_frequency__in=periods.keys()).select_related('user'):
        pending = list(Alert.objects.filter(house__user=user_settings.user, email_sent=False)
                       .select_related('house').order_by('created_at'))
        if not pending or now - pending[0].created_at < periods[user_settings.alert_frequency]:
            continue
        by_email = {}
        for alert in pending:
            email = _alert_recipient(alert.house)
            if email:
                by_email.setdefault(email, []).append(alert)
        label = 'godzinowe' if user_settings.alert_frequency == 'hourly' else 'dzienne'
        for email, alerts in by_email.items():
            if _send_alert_message(user_settings.user, email, alerts, f"Podsumowanie {label} alertów ({len(alerts)})"):
                sent += 1
    return sent


def log_activity(user, action, model_name, object_id, description, request=None):
    """
    Loguje aktywność użytkownika
    """
    ip_address = None
    if request:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(',')[0]
        else:
            ip_address = request.META.get('REMOTE_ADDR')

    ActivityLog.objects.create(
        user=user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        description=description,
        ip_address=ip_address
    )


def calculate_energy_for_period(house, start_time, end_time, sensor_id=None):
    """
    Oblicza całkowitą energię (kWh) dla domu w danym okresie.
    """
    total_kwh = 0
    
    if sensor_id:
        sensors_query = house.sensors.filter(id=sensor_id)
    else:
        sensors_query = house.sensors.all()

    for sensor in sensors_query:
        data_list = list(
            SensorData.objects.filter(
                sensor=sensor,
                timestamp__gte=start_time,
                timestamp__lt=end_time
            ).order_by('timestamp')
        )

        sensor_wh = 0
        if len(data_list) > 1:
            for i in range(1, len(data_list)):
                dt_seconds = (data_list[i].timestamp - data_list[i - 1].timestamp).total_seconds()
                
                if dt_seconds > 0 and dt_seconds < (sensor.offline_threshold_seconds + 60):
                    power = float(data_list[i].power) if data_list[i].power else 0
                    sensor_wh += power * (dt_seconds / 3600.0)
        
        total_kwh += sensor_wh / 1000.0

    return total_kwh


def get_comparison_data(house, period='month'):
    """
    Porównuje zużycie energii między okresami
    """
    now = timezone.now()

    if period == 'day':
        current_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        previous_start = current_start - timedelta(days=1)
        previous_end = current_start
    elif period == 'week':
        current_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        previous_start = current_start - timedelta(weeks=1)
        previous_end = current_start
    else:  # month
        current_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_day = current_start - timedelta(days=1)
        previous_start = prev_month_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_end = current_start

    current_kwh = calculate_energy_for_period(house, current_start, now)
    previous_kwh = calculate_energy_for_period(house, previous_start, previous_end)

    if previous_kwh > 0:
        change_percent = ((current_kwh - previous_kwh) / previous_kwh) * 100
    elif current_kwh > 0:
        change_percent = 100.0
    else:
        change_percent = 0.0

    return {
        'current': current_kwh,
        'previous': previous_kwh,
        'change_percent': change_percent,
        'change_absolute': current_kwh - previous_kwh
    }


def predict_monthly_cost(house):
    """
    Przewiduje koszt na koniec miesiąca
    """
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = monthrange(now.year, now.month)[1]
    
    progress_of_month = (now.day - 1 + (now.hour / 24.0) + (now.minute / (24.0 * 60.0))) / days_in_month

    if progress_of_month == 0:
        return {
            'current_kwh': 0, 'predicted_kwh': 0, 'predicted_cost': 0,
            'days_passed': 0, 'days_remaining': days_in_month, 'daily_average': 0
        }

    current_kwh = calculate_energy_for_period(house, start_of_month, now)
    
    predicted_kwh = current_kwh / progress_of_month
    predicted_cost = predicted_kwh * house.price_per_kwh
    
    days_passed = (now - start_of_month).total_seconds() / (24 * 3600.0)
    daily_avg = current_kwh / days_passed if days_passed > 0 else 0

    return {
        'current_kwh': current_kwh,
        'predicted_kwh': predicted_kwh,
        'predicted_cost': predicted_cost,
        'days_passed': days_passed,
        'days_remaining': days_in_month - days_passed,
        'daily_average': daily_avg
    }

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from sensors.models import Sensor, Alert
from sensors.utils import send_alert_email, send_alert_digests

class Command(BaseCommand):
    help = 'Sprawdza sensory, które są offline i tworzy alerty'

    def handle(self, *args, **options):
        self.stdout.write("Rozpoczynam sprawdzanie statusu czujników...")
        
        # Pobierz wszystkie aktywne czujniki
        active_sensors = Sensor.objects.filter(is_active=True)
        now = timezone.now()
        
        sensors_offline = 0
        sensors_online = 0

        for sensor in active_sensors:
            # Sprawdź ostatni pomiar
            last_reading = sensor.data.order_by('-timestamp').first()
            
            is_offline = True # Zakładamy, że jest offline, chyba że znajdziemy dowód
            
            if last_reading:
                # Oblicz różnicę czasu
                time_diff = now - last_reading.timestamp
                
                # Użyj progu zdefiniowanego przez użytkownika w modelu
                if time_diff < timedelta(seconds=sensor.offline_threshold_seconds):
                    is_offline = False
            
            # Czujnik, który jeszcze nigdy nie wysłał pomiaru (np. dopiero dodany),
            # liczymy jako offline, ale nie tworzymy dla niego alertu.
            if last_reading is None:
                sensors_offline += 1
                continue

            if is_offline:
                sensors_offline += 1
                # Czujnik jest offline. Sprawdź, czy już istnieje aktywny alert.
                active_alert_exists = Alert.objects.filter(
                    sensor=sensor,
                    alert_type='sensor_offline',
                    is_resolved=False # Szukamy nierozwiązanego alertu
                ).exists()
                
                if not active_alert_exists:
                    # Utwórz nowy alert, bo czujnik właśnie przeszedł w stan offline
                    alert = Alert.objects.create(
                        house=sensor.house,
                        sensor=sensor,
                        alert_type='sensor_offline',
                        severity='critical',
                        message=f"Czujnik '{sensor.name}' jest offline! (Brak danych przez ponad {sensor.offline_threshold_seconds}s)"
                    )
                    send_alert_email([alert])
                    self.stdout.write(self.style.WARNING(f"ALERT: Czujnik '{sensor.name}' jest OFFLINE."))
            
            else:
                sensors_online += 1
                # Czujnik jest online. Sprawdź, czy był wcześniej alert i go rozwiąż.
                resolved = Alert.objects.filter(
                    sensor=sensor,
                    alert_type='sensor_offline',
                    is_resolved=False
                ).update(is_resolved=True, is_read=True)
                if resolved:
                    self.stdout.write(self.style.SUCCESS(f"OK: Czujnik '{sensor.name}' wrócił ONLINE. Rozwiązano alert."))

        digests = send_alert_digests()
        self.stdout.write(self.style.SUCCESS(
            f"Zakończono. Online: {sensors_online}, Offline: {sensors_offline}, podsumowań email: {digests}"
        ))

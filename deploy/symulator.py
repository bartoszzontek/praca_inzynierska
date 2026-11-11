"""
Symulator modułu pomiarowego (ESP8266 + PZEM-004T) do testów wdrożenia.

Co 15 sekund wysyła pomiar w tym samym formacie i na ten sam adres co prawdziwy
moduł, więc sprawdza cały łańcuch: Cloudflare -> tunel -> Nginx -> Django -> baza.
Uruchamiany jako kontener `simulator` (profil "demo" w docker-compose.yml).

Przy starcie sam przygotowuje w bazie dom i czujnik SIM-001 (przypisane do pierwszego
superużytkownika) i wysyła pomiary z kluczem API tego czujnika – tak jak prawdziwy moduł.
"""
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # katalog projektu
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from sensors.models import House, Sensor  # noqa: E402

URL = os.environ.get('SIM_URL', 'https://energymonitor.fun/dashboard/admin/sensor/readings/')
SENSOR_ID = os.environ.get('SIM_SENSOR_ID', 'SIM-001')
INTERVAL = int(os.environ.get('SIM_INTERVAL', '15'))


def log(msg):
    print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


def prepare():
    """Zwraca klucz API czujnika; czeka, aż w bazie pojawi się konto administratora."""
    while True:
        owner = User.objects.filter(is_superuser=True).order_by('id').first()
        if owner:
            break
        log('Brak konta administratora - czekam 60 s (python manage.py createsuperuser)')
        time.sleep(60)

    # konto techniczne z wcześniejszej wersji symulatora nie jest już potrzebne
    User.objects.filter(username='symulator', is_superuser=False).delete()

    sensor = Sensor.objects.filter(sensor_id=SENSOR_ID).first()
    if sensor is None:
        house, _ = House.objects.get_or_create(
            user=owner, name='Dom (symulacja)',
            defaults={'address': 'dane testowe z symulatora', 'price_per_kwh': 1.10},
        )
        sensor = Sensor.objects.create(
            sensor_id=SENSOR_ID, house=house, name='Symulator - obwód gniazd', location='Kuchnia',
            description='Czujnik wirtualny generujący dane testowe',
        )
    log(f'Gotowe: czujnik={SENSOR_ID} (dom: {sensor.house.name}), adres={URL}')
    return sensor.api_token


class Household:
    """Prosty model zużycia: lodówka, odbiorniki stałe, pora dnia i losowe urządzenia."""

    APPLIANCES = [  # nazwa, moc [W], czas pracy [s], prawdopodobieństwo startu na krok
        ('czajnik', 2000, 180, 0.004),
        ('mikrofalówka', 1100, 150, 0.002),
        ('suszarka', 1600, 420, 0.0008),
        ('odkurzacz', 900, 900, 0.0004),
    ]

    def __init__(self):
        self.running = {}      # nazwa -> pozostały czas
        self.fridge_on = False
        self.fridge_left = 0
        self.energy_kwh = 0.0  # licznik jak w PZEM-004T

    def step(self, dt):
        hour = datetime.now().hour + datetime.now().minute / 60
        # odbiorniki stałe + wieczorne oświetlenie i RTV
        base = 60 + random.uniform(-5, 5)
        evening = 250 * max(0.0, math.sin(math.pi * (hour - 16) / 8)) if 16 <= hour <= 24 else 0
        morning = 120 if 6 <= hour < 8.5 else 0
        # lodówka: cykl ok. 15 min pracy / 25 min przerwy
        self.fridge_left -= dt
        if self.fridge_left <= 0:
            self.fridge_on = not self.fridge_on
            self.fridge_left = random.uniform(700, 1100) if self.fridge_on else random.uniform(1200, 1800)
        fridge = 110 if self.fridge_on else 0
        # urządzenia uruchamiane losowo (częściej rano i wieczorem)
        activity = 3.0 if (6 <= hour < 9 or 17 <= hour < 22) else (0.3 if hour < 6 else 1.0)
        for name, p, dur, prob in self.APPLIANCES:
            if name not in self.running and random.random() < prob * activity * dt / 15:
                self.running[name] = dur
        extra = 0
        for name in list(self.running):
            extra += dict((a[0], a[1]) for a in self.APPLIANCES)[name]
            self.running[name] -= dt
            if self.running[name] <= 0:
                del self.running[name]

        power = max(5.0, base + evening + morning + fridge + extra + random.gauss(0, 4))
        voltage = 231.0 + random.gauss(0, 0.8) - power / 1000 * 1.5
        pf = 0.99 if extra >= 900 else (0.62 + 0.25 * min(1.0, power / 400))
        pf = round(min(1.0, max(0.5, pf + random.gauss(0, 0.01))), 2)
        current = power / (voltage * pf)
        self.energy_kwh += power * dt / 3_600_000
        return {
            'sensor_id': SENSOR_ID,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'voltage': round(voltage, 1),
            'current': round(current, 3),
            'power': round(power, 1),
            'energy': round(self.energy_kwh, 3),
            'frequency': round(50.0 + random.gauss(0, 0.02), 2),
            'pf': pf,
        }


def send(token, reading):
    req = urllib.request.Request(
        URL, data=json.dumps([reading]).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Token {token}',
                 'User-Agent': 'EnergyMonitor-Symulator/1.0'},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def main():
    token = prepare()
    house = Household()
    ok = 0
    while True:
        reading = house.step(INTERVAL)
        try:
            status = send(token, reading)
            ok += 1
            if ok % 20 == 1:  # co ok. 5 minut jedna linia w logu
                log(f'HTTP {status}  P={reading["power"]} W  U={reading["voltage"]} V  wysłanych={ok}')
        except urllib.error.HTTPError as e:
            log(f'Błąd HTTP {e.code}: {e.read()[:200]!r}')
        except Exception as e:  # brak sieci, timeout itp. - próbujemy dalej
            log(f'Błąd połączenia: {e}')
        time.sleep(INTERVAL)


if __name__ == '__main__':
    main()

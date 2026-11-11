from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from datetime import timedelta


class House(models.Model):
    """Model domu/lokalizacji"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='houses')
    name = models.CharField(max_length=100, verbose_name="Nazwa")
    address = models.CharField(max_length=255, blank=True, verbose_name="Adres")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data utworzenia")
    price_per_kwh = models.FloatField(default=0.80, verbose_name="Cena za kWh [PLN]")

    monthly_limit_kwh = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Limit miesięczny [kWh]",
        help_text="Alert gdy przekroczono limit"
    )
    alert_email = models.EmailField(
        blank=True,
        verbose_name="Email do alertów",
        help_text="Jeśli pusty, użyje email użytkownika"
    )

    class Meta:
        verbose_name = "Dom"
        verbose_name_plural = "Domy"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.user.username})"


class Sensor(models.Model):
    """Model czujnika PZEM-004T"""
    house = models.ForeignKey(House, on_delete=models.CASCADE, related_name='sensors')
    sensor_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        verbose_name="ID czujnika"
    )
    name = models.CharField(max_length=100, verbose_name="Nazwa")
    description = models.TextField(blank=True, verbose_name="Opis")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data dodania")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")

    location = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Lokalizacja",
        help_text="Np. 'Salon', 'Kuchnia', 'Piwnica'"
    )
    icon = models.CharField(
        max_length=50,
        default='microchip',
        verbose_name="Ikona",
        help_text="Nazwa ikony Font Awesome (bez 'fa-')"
    )
    color = models.CharField(
        max_length=7,
        default='#3b82f6',
        verbose_name="Kolor",
        help_text="Hex color, np. #3b82f6"
    )

    # === POLA DLA REGUŁ ALERTÓW ===
    power_threshold = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Próg mocy [W]",
        help_text="Alert gdy moc przekroczy tę wartość"
    )
    
    # NOWE POLE (Twoja prośba)
    current_max_threshold = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Max. prąd [A]",
        help_text="Alert gdy prąd (natężenie) wzrośnie powyżej tej wartości (np. 10)"
    )
    
    voltage_min_threshold = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Min. napięcie [V]",
        help_text="Alert gdy napięcie spadnie poniżej tej wartości"
    )
    voltage_max_threshold = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Max. napięcie [V]",
        help_text="Alert gdy napięcie wzrośnie powyżej tej wartości"
    )
    
    # Zmieniam domyślny czas na 30 sekund, zgodnie z Twoją prośbą o "5 sekund"
    offline_threshold_seconds = models.PositiveIntegerField(
        default=30, # Domyślnie 30 sekund
        verbose_name="Próg offline [s]",
        help_text="Po ilu sekundach bez pomiaru uznać czujnik za offline (np. 30)"
    )
    # === KONIEC PÓL REGUŁ ===

    class Meta:
        verbose_name = "Czujnik"
        verbose_name_plural = "Czujniki"
        ordering = ['house', 'name']

    def __str__(self):
        return f"{self.name} - {self.house.name}"

    @property
    def is_online(self):
        """
        Sprawdza czy czujnik jest online na podstawie
        KONFIGUROWALNEGO progu 'offline_threshold_seconds'.
        """
        last_reading = self.data.order_by('-timestamp').first()
        if last_reading:
            # Użyj progu zdefiniowanego przez użytkownika
            return (timezone.now() - last_reading.timestamp) < timedelta(seconds=self.offline_threshold_seconds)
        return False


class SensorData(models.Model):
    """Dane z czujnika PZEM-004T v3"""
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='data')
    timestamp = models.DateTimeField(verbose_name="Czas pomiaru", db_index=True)

    voltage = models.FloatField(null=True, blank=True, verbose_name="Napięcie [V]")
    current = models.FloatField(null=True, blank=True, verbose_name="Prąd [A]")
    power = models.FloatField(null=True, blank=True, verbose_name="Moc czynna [W]")
    energy = models.FloatField(null=True, blank=True, verbose_name="Energia [kWh]")
    frequency = models.FloatField(null=True, blank=True, verbose_name="Częstotliwość [Hz]")
    pf = models.FloatField(null=True, blank=True, verbose_name="Współczynnik mocy")

    reactive_power = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Moc bierna [VAR]",
        help_text="Obliczona na podstawie mocy czynnej i współczynnika mocy"
    )

    class Meta:
        verbose_name = "Pomiar"
        verbose_name_plural = "Pomiary"
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['sensor', '-timestamp']),
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"{self.sensor.name} @ {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"

    @property
    def apparent_power(self):
        """Oblicza moc pozorną S [VA]"""
        if self.pf and self.pf != 0 and self.power:
            return self.power / self.pf
        return 0


class Alert(models.Model):
    """Model alertów/powiadomień"""
    ALERT_TYPES = [
        ('power_high', 'Przekroczono próg mocy'),
        ('current_high', 'Przekroczono próg prądu'), # NOWY TYP
        ('voltage_anomaly', 'Anomalia napięcia'), 
        ('monthly_limit', 'Przekroczono limit miesięczny'),
        ('sensor_offline', 'Czujnik offline'),
        ('sensor_online', 'Czujnik znów online'),
        ('anomaly', 'Inna anomalia'),
    ]

    SEVERITY = [
        ('info', 'Informacja'),
        ('warning', 'Ostrzeżenie'),
        ('critical', 'Krytyczny'),
    ]

    house = models.ForeignKey(House, on_delete=models.CASCADE, related_name='alerts')
    sensor = models.ForeignKey(
        Sensor,
        on_delete=models.CASCADE,
        related_name='alerts',
        null=True,
        blank=True
    )
    alert_type = models.CharField(max_length=20, choices=ALERT_TYPES, verbose_name="Typ alertu")
    severity = models.CharField(max_length=10, choices=SEVERITY, default='warning', verbose_name="Ważność")
    message = models.TextField(verbose_name="Wiadomość")
    value = models.FloatField(null=True, blank=True, verbose_name="Wartość")
    threshold = models.FloatField(null=True, blank=True, verbose_name="Próg")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data utworzenia")
    is_read = models.BooleanField(default=False, verbose_name="Przeczytane")
    is_resolved = models.BooleanField(default=False, verbose_name="Rozwiązane")
    email_sent = models.BooleanField(default=False, verbose_name="Email wysłany")

    class Meta:
        verbose_name = "Alert"
        verbose_name_plural = "Alerty"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['house', '-created_at']),
            models.Index(fields=['is_read', 'is_resolved']),
        ]

    def __str__(self):
        return f"{self.get_alert_type_display()} - {self.house.name} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"


class UserSettings(models.Model):
    """Ustawienia użytkownika"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='settings')

    theme = models.CharField(
        max_length=10,
        choices=[('dark', 'Ciemny'), ('light', 'Jasny')],
        default='dark',
        verbose_name="Motyw"
    )
    email_alerts = models.BooleanField(default=True, verbose_name="Alerty email")
    alert_frequency = models.CharField(
        max_length=20,
        choices=[
            ('immediate', 'Natychmiast'),
            ('hourly', 'Co godzinę'),
            ('daily', 'Dziennie'),
        ],
        default='immediate',
        verbose_name="Częstotliwość alertów"
    )
    live_refresh_interval = models.IntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
        verbose_name="Odświeżanie [s]",
        help_text="Co ile sekund odświeżać widget live (1-60)"
    )
    show_predictions = models.BooleanField(default=True, verbose_name="Pokazuj predykcje")
    monthly_goal_kwh = models.FloatField(
        null=True,
        blank=True,
        verbose_name="Cel miesięczny [kWh]"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ustawienia użytkownika"
        verbose_name_plural = "Ustawienia użytkowników"

    def __str__(self):
        return f"Ustawienia - {self.user.username}"


class ActivityLog(models.Model):
    """Historia zmian (audit log)"""
    ACTION_TYPES = [
        ('create', 'Utworzono'),
        ('update', 'Zaktualizowano'),
        ('delete', 'Usunięto'),
        ('assign', 'Przypisano'),
        ('alert', 'Alert'),
        ('login', 'Logowanie'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='activity_logs')
    action = models.CharField(max_length=20, choices=ACTION_TYPES, verbose_name="Akcja")
    model_name = models.CharField(max_length=50, verbose_name="Model")
    object_id = models.IntegerField(verbose_name="ID obiektu")
    description = models.TextField(verbose_name="Opis")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Adres IP")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")

    class Meta:
        verbose_name = "Log aktywności"
        verbose_name_plural = "Logi aktywności"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f"{self.get_action_display()} - {self.model_name} #{self.object_id} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"

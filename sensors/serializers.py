from rest_framework import serializers
from datetime import timezone
from django.contrib.auth.models import User
from .models import House, Sensor, SensorData, Alert, UserSettings


class SensorReadingSerializer(serializers.Serializer):
    """Serializer dla odczytów z czujnika"""
    sensor_id = serializers.CharField()
    timestamp = serializers.DateTimeField()
    voltage = serializers.FloatField()
    current = serializers.FloatField()
    power = serializers.FloatField()
    energy = serializers.FloatField()
    frequency = serializers.FloatField()
    pf = serializers.FloatField()


# --- NOWY SERIALIZER ---
class UserSettingsSerializer(serializers.ModelSerializer):
    """Serializer dla modelu UserSettings"""
    class Meta:
        model = UserSettings
        fields = [
            'id', 'theme', 'email_alerts', 'alert_frequency',
            'live_refresh_interval', 'monthly_goal_kwh'
        ]

# --- NOWY SERIALIZER ---
class UserSerializer(serializers.ModelSerializer):
    """Serializer dla modelu User, zagnieżdża UserSettings"""
    # Używamy 'settings', bo tak nazwałeś related_name w UserSettings
    settings = UserSettingsSerializer(read_only=True) 

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'is_staff', 'date_joined', 'settings'
        ]

# --- ZMODYFIKOWANY SERIALIZER ---
class SensorSerializer(serializers.ModelSerializer):
    """Serializer dla modelu Sensor"""
    is_online = serializers.SerializerMethodField()
    last_reading = serializers.SerializerMethodField()

    class Meta:
        model = Sensor
        fields = [
            'id', 'house', 'sensor_id', 'name', 'description',
            'location', 'icon', 'color', 'is_active', 'power_threshold',
            'current_max_threshold', 'voltage_min_threshold', 'voltage_max_threshold',
            'offline_threshold_seconds',
            'created_at', 'is_online', 'last_reading'
        ]
        read_only_fields = ['created_at']

    def get_is_online(self, obj):
        return obj.is_online

    def get_last_reading(self, obj):
        last = obj.data.order_by('-timestamp').first()
        if last:
            return {
                'timestamp': last.timestamp,
                'power': last.power,
            }
        return None

# --- ZMODYFIKOWANY SERIALIZER ---
class HouseSerializer(serializers.ModelSerializer):
    """Serializer dla modelu House Z ZAGNIEŻDŻONYMI CZUJNIKAMI"""
    sensor_count = serializers.SerializerMethodField()
    # Zagnieżdżamy SensorSerializer
    sensors = SensorSerializer(many=True, read_only=True) 

    class Meta:
        model = House
        fields = [
            'id', 'user', 'name', 'address', 'price_per_kwh',
            'monthly_limit_kwh', 'alert_email', 'created_at', 
            'sensor_count', 'sensors'  # Dodajemy 'sensors'
        ]
        read_only_fields = ['created_at']

    def get_sensor_count(self, obj):
        return obj.sensors.count()


class SensorDataSerializer(serializers.ModelSerializer):
    """Serializer dla modelu SensorData"""
    timestamp = serializers.DateTimeField(
        format="%Y-%m-%dT%H:%M:%SZ",  # UTC z oznaczeniem strefy – poprawny czas w przeglądarce
        default_timezone=timezone.utc,
        read_only=True
    )

    class Meta:
        model = SensorData
        fields = [
            'id', 'sensor', 'timestamp', 'voltage', 'current',
            'power', 'energy', 'frequency', 'pf', 'reactive_power'
        ]
        read_only_fields = ['timestamp', 'reactive_power']


class AlertSerializer(serializers.ModelSerializer):
    """Serializer dla modelu Alert"""
    alert_type_display = serializers.CharField(source='get_alert_type_display', read_only=True)
    severity_display = serializers.CharField(source='get_severity_display', read_only=True)
    house_name = serializers.CharField(source='house.name', read_only=True)
    sensor_name = serializers.CharField(source='sensor.name', read_only=True, allow_null=True)

    class Meta:
        model = Alert
        fields = [
            'id', 'house', 'house_name', 'sensor', 'sensor_name',
            'alert_type', 'alert_type_display', 'severity', 'severity_display',
            'message', 'value', 'threshold', 'created_at',
            'is_read', 'is_resolved', 'email_sent'
        ]
        read_only_fields = ['created_at']

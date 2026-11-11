from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import House, Sensor, SensorData, Alert, UserSettings, ActivityLog
from django.db.models import Avg, Max
from django.contrib.admin import SimpleListFilter
from django.utils import timezone
from datetime import timedelta


# --- LISTA FILTRÓW DLA ADMINA ---
class OnlineStatusFilter(SimpleListFilter):
    title = 'Status Online'
    parameter_name = 'is_online_status'

    def lookups(self, request, model_admin):
        return [
            ('online', 'Online (aktywne)'),
            ('offline', 'Offline (brak danych)'),
        ]

    def queryset(self, request, queryset):
        if self.value() == 'online':
            # Używamy filter(pk__in=...) do bezpiecznego filtrowania po is_online
            return queryset.filter(pk__in=[s.pk for s in queryset if s.is_online])
        if self.value() == 'offline':
            return queryset.filter(pk__in=[s.pk for s in queryset if not s.is_online])
        return queryset


class SensorInline(admin.TabularInline):
    """Inline dla czujników"""
    model = Sensor
    extra = 1
    fields = ('sensor_id', 'name', 'location', 'is_active', 'power_threshold', 
              'current_max_threshold', 'voltage_min_threshold', 'voltage_max_threshold', 
              'offline_threshold_seconds')
    readonly_fields = ()


class AlertInline(admin.TabularInline):
    """Inline dla alertów"""
    model = Alert
    extra = 0
    fields = ('alert_type', 'severity', 'message', 'is_read', 'is_resolved', 'created_at')
    readonly_fields = ('created_at',)
    can_delete = False
    max_num = 5

    def get_queryset(self, request):
        return super().get_queryset(request).order_by('-created_at')


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'user',
        'address',
        'price_per_kwh',
        'monthly_limit_kwh',
        'sensor_count',
        'status_badge',
        'created_at'
    )
    list_filter = ('user', 'created_at')
    search_fields = ('name', 'address', 'user__username', 'user__email')
    inlines = [SensorInline, AlertInline]
    readonly_fields = ('created_at', 'get_monthly_usage', 'get_current_power')

    fieldsets = (
        ('Informacje podstawowe', {
            'fields': ('user', 'name', 'address')
        }),
        ('Ustawienia finansowe', {
            'fields': ('price_per_kwh', 'monthly_limit_kwh')
        }),
        ('Alerty', {
            'fields': ('alert_email',)
        }),
        ('Statystyki', {
            'fields': ('get_monthly_usage', 'get_current_power'),
            'classes': ('collapse',)
        }),
        ('Informacje systemowe', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def sensor_count(self, obj):
        count = obj.sensors.count()
        return format_html(
            '<span style="background: #3b82f6; color: white; padding: 2px 8px; border-radius: 4px;">{}</span>',
            count
        )

    sensor_count.short_description = 'Czujniki'

    def status_badge(self, obj):
        online = sum(1 for s in obj.sensors.all() if s.is_online)
        total = obj.sensors.count()
        if total == 0:
            return format_html('<span style="color: #94a3b8;">Brak czujników</span>')
        if online == total:
            return format_html(
                '<span style="background: #10b981; color: white; padding: 2px 8px; border-radius: 4px;">✓ Online</span>'
            )
        return format_html(
            '<span style="background: #f59e0b; color: white; padding: 2px 8px; border-radius: 4px;">{}/{}</span>',
            online, total
        )

    status_badge.short_description = 'Status'

    def get_monthly_usage(self, obj):
        from .utils import calculate_energy_for_period
        now = timezone.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        try:
            total_kwh = calculate_energy_for_period(obj, start, now)
        except ImportError:
            total_kwh = 0

        cost = total_kwh * obj.price_per_kwh
        return format_html(
            '<strong>{} kWh</strong> ({} PLN)',
            f"{total_kwh:.2f}", f"{cost:.2f}"
        )

    get_monthly_usage.short_description = 'Zużycie w miesiącu'

    def get_current_power(self, obj):
        total_power = 0
        for sensor in obj.sensors.all():
            if sensor.is_online:
                last = sensor.data.order_by('-timestamp').first()
                if last and last.power:
                    total_power += float(last.power)
        return format_html('<strong>{} W</strong>', f"{total_power:.0f}")

    get_current_power.short_description = 'Aktualna moc'


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'sensor_id',
        'house',
        'location',
        'online_status',
        'current_power',
        'is_active',
        'created_at'
    )
    list_filter = (OnlineStatusFilter, 'is_active', 'house', 'created_at')
    search_fields = ('sensor_id', 'name', 'house__name', 'location', 'description')
    readonly_fields = ('created_at', 'get_last_reading', 'get_statistics')

    fieldsets = (
        ('Informacje podstawowe', {
            'fields': ('house', 'sensor_id', 'name', 'description', 'api_token')
        }),
        ('Lokalizacja i wygląd', {
            'fields': ('location', 'icon', 'color')
        }),
        ('Alerty (Reguły)', {
            'fields': (
                'power_threshold', 
                'current_max_threshold',
                ('voltage_min_threshold', 'voltage_max_threshold'),
                'offline_threshold_seconds'
            )
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Statystyki', {
            'fields': ('get_last_reading', 'get_statistics'),
            'classes': ('collapse',)
        }),
        ('Informacje systemowe', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def online_status(self, obj):
        if obj.is_online:
            return format_html(
                '<span style="color: #10b981;">● Online</span>'
            )
        return format_html(
            '<span style="color: #ef4444;">● Offline</span>'
        )

    online_status.short_description = 'Status'

    def current_power(self, obj):
        last = obj.data.order_by('-timestamp').first()
        if last and last.power and obj.is_online:
            return format_html('<strong>{} W</strong>', f"{last.power:.1f}")
        return '-'

    current_power.short_description = 'Moc'

    def get_last_reading(self, obj):
        last = obj.data.order_by('-timestamp').first()
        if last:
            return format_html(
                '<strong>Czas:</strong> {}<br>'
                '<strong>Napięcie:</strong> {} V<br>'
                '<strong>Prąd:</strong> {} A<br>'
                '<strong>Moc:</strong> {} W<br>'
                '<strong>PF:</strong> {}',
                last.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                f"{last.voltage or 0:.1f}",
                f"{last.current or 0:.2f}",
                f"{last.power or 0:.1f}",
                f"{last.pf or 0:.2f}"
            )
        return 'Brak danych'

    get_last_reading.short_description = 'Ostatni odczyt'

    def get_statistics(self, obj):
        now = timezone.now()
        day_ago = now - timedelta(days=1)

        data_24h = obj.data.filter(timestamp__gte=day_ago)
        count = data_24h.count()
        
        if count > 1:
            avg_power = data_24h.aggregate(avg=Avg('power'))['avg'] or 0
            max_power = data_24h.aggregate(max=Max('power'))['max'] or 0
            return format_html(
                '<strong>Pomiary 24h:</strong> {}<br>'
                '<strong>Śr. moc:</strong> {} W<br>'
                '<strong>Max moc:</strong> {} W',
                count, 
                f"{avg_power:.1f}", 
                f"{max_power:.1f}"
            )
        return 'Brak danych z ostatnich 24h'

    get_statistics.short_description = 'Statystyki 24h'


@admin.register(SensorData)
class SensorDataAdmin(admin.ModelAdmin):
    list_display = (
        'sensor',
        'timestamp',
        'voltage',
        'current',
        'power',
        'reactive_power',
        'energy',
        'frequency',
        'pf'
    )
    list_filter = ('sensor', 'timestamp')
    search_fields = ('sensor__sensor_id', 'sensor__name')
    readonly_fields = ('timestamp', 'reactive_power')
    date_hierarchy = 'timestamp'

    fieldsets = (
        ('Czujnik', {
            'fields': ('sensor', 'timestamp')
        }),
        ('Parametry elektryczne', {
            'fields': (
                ('voltage', 'current'),
                ('power', 'reactive_power'),
                ('frequency', 'pf'),
                'energy'
            )
        }),
    )

    def has_add_permission(self, request):
        return False


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        'severity_badge',
        'alert_type',
        'house',
        'sensor',
        'short_message',
        'value_display',
        'status_badges',
        'created_at'
    )
    list_filter = ('severity', 'alert_type', 'is_read', 'is_resolved', 'created_at')
    search_fields = ('house__name', 'sensor__name', 'message')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Podstawowe informacje', {
            'fields': ('house', 'sensor', 'alert_type', 'severity')
        }),
        ('Szczegóły', {
            'fields': ('message', 'value', 'threshold')
        }),
        ('Status', {
            'fields': ('is_read', 'is_resolved', 'email_sent')
        }),
        ('Meta', {
            'fields': ('created_at',)
        }),
    )

    actions = ['mark_as_read', 'mark_as_resolved']

    def severity_badge(self, obj):
        colors = {
            'info': '#3b82f6',
            'warning': '#f59e0b',
            'critical': '#ef4444'
        }
        return format_html(
            '<span style="background: {}; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold;">{}</span>',
            colors.get(obj.severity, '#94a3b8'),
            obj.get_severity_display().upper()
        )

    severity_badge.short_description = 'Ważność'

    def short_message(self, obj):
        if len(obj.message) > 50:
            return obj.message[:50] + '...'
        return obj.message

    def value_display(self, obj):
        """Bezpiecznie formatuje wartość i próg, nawet jeśli są None."""
        value_str = '-'
        threshold_str = '-'

        if obj.value is not None:
            value_str = f"{obj.value:.1f}"
            
        if obj.threshold is not None:
            threshold_str = f"{obj.threshold:.1f}"

        if obj.value is not None and obj.threshold is not None:
            return f"{value_str} / {threshold_str}"
        elif obj.value is not None:
            return value_str
        else:
            return '-'

    value_display.short_description = 'Wartość/Próg'

    def status_badges(self, obj):
        badges = []
        if obj.is_read:
            badges.append(
                '<span style="background: #10b981; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;">✓</span>')
        if obj.is_resolved:
            badges.append(
                '<span style="background: #3b82f6; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;">OK</span>')
        if obj.email_sent:
            badges.append(
                '<span style="background: #8b5cf6; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;">✉</span>')
        return format_html(' '.join(badges)) if badges else '-'

    status_badges.short_description = 'Status'

    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)
        self.message_user(request, f"Oznaczono {queryset.count()} alertów jako przeczytane")

    mark_as_read.short_description = "Oznacz jako przeczytane"

    def mark_as_resolved(self, request, queryset):
        queryset.update(is_resolved=True)
        self.message_user(request, f"Rozwiązano {queryset.count()} alertów")

    mark_as_resolved.short_description = "Oznacz jako rozwiązane"


@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = ('user', 'theme', 'email_alerts', 'alert_frequency', 'monthly_goal_kwh', 'updated_at')
    list_filter = ('theme', 'email_alerts', 'alert_frequency')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        ('Użytkownik', {
            'fields': ('user',)
        }),
        ('Personalizacja', {
            'fields': ('theme', 'live_refresh_interval')
        }),
        ('Alerty', {
            'fields': ('email_alerts', 'alert_frequency')
        }),
        ('Cel miesięczny', {
            'fields': ('monthly_goal_kwh',)
        }),
        ('Meta', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('action_badge', 'user', 'model_name', 'object_id', 'short_description', 'ip_address', 'created_at')
    list_filter = ('action', 'model_name', 'created_at')
    search_fields = ('user__username', 'description', 'model_name')
    readonly_fields = ('user', 'action', 'model_name', 'object_id', 'description', 'ip_address', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def action_badge(self, obj):
        colors = {
            'create': '#10b981',
            'update': '#3b82f6',
            'delete': '#ef4444',
            'assign': '#8b5cf6',
            'alert': '#f59e0b'
        }
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            colors.get(obj.action, '#94a3b8'),
            obj.get_action_display()
        )

    action_badge.short_description = 'Akcja'

    def short_description(self, obj):
        if len(obj.description) > 60:
            return obj.description[:60] + '...'
        return obj.description

    short_description.short_description = 'Opis'

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import House, Sensor

class CustomUserCreationForm(UserCreationForm):
    """
    Niestandardowy formularz rejestracji, który wymaga podania adresu e-mail
    i sprawdza jego unikalność.
    """
    email = forms.EmailField(
        required=True, 
        help_text='Adres e-mail jest wymagany.',
        widget=forms.EmailInput(attrs={'placeholder': 'twoj@email.com'})
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ('email',)

    def clean_email(self):
        """
        Sprawdza, czy email jest unikalny.
        """
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise forms.ValidationError("Użytkownik z tym adresem e-mail już istnieje.")
        return email

    def __init__(self, *args, **kwargs):
        """
        Aktualizuje pola formularza, aby pasowały do stylizacji z login.html
        """
        super().__init__(*args, **kwargs)
        
        placeholders = {
            'username': 'Wybierz nazwę użytkownika',
            'email': 'twoj@email.com',
            'password1': 'Minimum 8 znaków',
            'password2': 'Powtórz hasło',
        }
        
        for field_name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'
            if field_name in placeholders:
                field.widget.attrs['placeholder'] = placeholders[field_name]
            
            if field_name == 'password1':
                field.label = 'Hasło'
            if field_name == 'password2':
                field.label = 'Potwierdź hasło'

SENSOR_ICONS = [
    ('microchip', 'Moduł pomiarowy'),
    ('plug', 'Gniazdko'),
    ('bolt', 'Obwód'),
    ('snowflake', 'Lodówka / zamrażarka'),
    ('fire', 'Kuchenka / piekarnik'),
    ('tv', 'RTV'),
    ('desktop', 'Komputer'),
    ('lightbulb', 'Oświetlenie'),
    ('shirt', 'Pralka / suszarka'),
    ('temperature-high', 'Ogrzewanie'),
]


class StyledFormMixin:
    """Dodaje klasę CSS do wszystkich pól formularza."""

    def _style_fields(self):
        for field in self.fields.values():
            widget = field.widget
            if not isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                widget.attrs['class'] = (widget.attrs.get('class', '') + ' input').strip()


class SensorSettingsForm(StyledFormMixin, forms.ModelForm):
    """Ustawienia czujnika edytowane przez właściciela domu."""

    icon = forms.ChoiceField(choices=SENSOR_ICONS, label='Ikona')

    class Meta:
        model = Sensor
        fields = ['name', 'location', 'description', 'icon', 'color',
                  'power_threshold', 'current_max_threshold',
                  'voltage_min_threshold', 'voltage_max_threshold',
                  'offline_threshold_seconds']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
            'color': forms.TextInput(attrs={'type': 'color'}),
        }
        help_texts = {
            'location': 'Np. Kuchnia, Salon, Garaż',
            'color': 'Kolor ikony czujnika',
        }
        labels = {
            'power_threshold': 'Próg mocy [W]',
            'current_max_threshold': 'Maks. prąd [A]',
            'voltage_min_threshold': 'Min. napięcie [V]',
            'voltage_max_threshold': 'Maks. napięcie [V]',
            'offline_threshold_seconds': 'Brak danych po [s]',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields['offline_threshold_seconds'].min_value = 20
        self.fields['offline_threshold_seconds'].widget.attrs['min'] = 20

    def clean(self):
        cleaned = super().clean()
        vmin, vmax = cleaned.get('voltage_min_threshold'), cleaned.get('voltage_max_threshold')
        if vmin is not None and vmax is not None and vmin >= vmax:
            self.add_error('voltage_max_threshold', 'Maksymalne napięcie musi być większe od minimalnego.')
        for name in ('power_threshold', 'current_max_threshold', 'voltage_min_threshold', 'voltage_max_threshold'):
            value = cleaned.get(name)
            if value is not None and value <= 0:
                self.add_error(name, 'Wartość musi być dodatnia (puste pole wyłącza alert).')
        return cleaned


class HouseSettingsForm(StyledFormMixin, forms.ModelForm):
    """Ustawienia domu edytowane przez właściciela."""

    class Meta:
        model = House
        fields = ['name', 'address', 'price_per_kwh', 'monthly_limit_kwh', 'alert_email']
        labels = {
            'price_per_kwh': 'Cena za kWh [zł]',
            'monthly_limit_kwh': 'Limit miesięczny [kWh]',
            'alert_email': 'Email do alertów',
        }
        help_texts = {
            'monthly_limit_kwh': 'Po przekroczeniu powstaje alert. Puste pole wyłącza limit.',
            'alert_email': 'Jeśli puste, alerty trafiają na adres z konta.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields['price_per_kwh'].widget.attrs.update({'step': '0.01', 'min': '0'})

    def clean_price_per_kwh(self):
        price = self.cleaned_data['price_per_kwh']
        if price is None or price < 0:
            raise forms.ValidationError('Cena nie może być ujemna.')
        return price

    def clean_monthly_limit_kwh(self):
        limit = self.cleaned_data.get('monthly_limit_kwh')
        if limit is not None and limit <= 0:
            raise forms.ValidationError('Limit musi być dodatni.')
        return limit


class AdminSensorForm(StyledFormMixin, forms.ModelForm):
    """Dodawanie czujnika przez administratora."""

    class Meta:
        model = Sensor
        fields = ['house', 'sensor_id', 'name', 'location']
        labels = {'house': 'Dom', 'sensor_id': 'Identyfikator czujnika (np. ESP-001)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields['sensor_id'].required = True
        self.fields['house'].queryset = House.objects.select_related('user').order_by('user__username', 'name')
        self.fields['house'].label_from_instance = lambda h: f"{h.name} ({h.user.username})"

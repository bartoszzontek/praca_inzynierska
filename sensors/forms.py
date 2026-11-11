from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Alert, House, Sensor

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

# --- NOWY FORMULARZ ---
class AlertForm(forms.ModelForm):
    """Formularz do ręcznego tworzenia alertów przez użytkownika."""
    
    class Meta:
        model = Alert
        fields = ['house', 'sensor', 'alert_type', 'severity', 'message', 'value', 'threshold']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        # Pobierz zalogowanego użytkownika przekazanego z widoku
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if self.user:
            # Filtruj listę domów, aby pokazać tylko te należące do użytkownika
            self.fields['house'].queryset = House.objects.filter(user=self.user)
            
            # Filtruj listę czujników, aby pokazać tylko te z domów użytkownika
            self.fields['sensor'].queryset = Sensor.objects.filter(house__user=self.user)

        # Ustaw pole 'sensor' jako nieobowiązkowe (alert może dotyczyć całego domu)
        self.fields['sensor'].required = False

        # Dodaj klasy CSS do wszystkich pól
        for field_name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned = super().clean()
        house = cleaned.get('house')
        sensor = cleaned.get('sensor')
        if house and sensor and sensor.house_id != house.id:
            self.add_error('sensor', 'Wybrany czujnik nie należy do wybranego domu.')
        return cleaned

from .models import UserSettings


def user_theme(request):
    """Udostępnia szablonom motyw wybrany przez zalogowanego użytkownika."""
    theme = 'dark'
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        settings = UserSettings.objects.filter(user=user).only('theme').first()
        if settings:
            theme = settings.theme
    return {'user_theme': theme}

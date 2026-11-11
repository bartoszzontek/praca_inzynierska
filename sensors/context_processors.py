from .models import Alert, UserSettings


def user_theme(request):
    """Motyw zalogowanego użytkownika i liczba nieprzeczytanych alertów (pasek nawigacji)."""
    theme = 'dark'
    unread = 0
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        settings = UserSettings.objects.filter(user=user).only('theme').first()
        if settings:
            theme = settings.theme
        unread = Alert.objects.filter(house__user=user, is_read=False).count()
    return {'user_theme': theme, 'unread_alerts_count': unread}

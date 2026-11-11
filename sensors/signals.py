from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .utils import log_activity


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """Zapisuje logowanie w dzienniku aktywności (widocznym w panelu administratora)."""
    log_activity(user=user, action='login', model_name='User', object_id=user.id,
                 description=f"Logowanie użytkownika {user.username}", request=request)

import secrets

from django.db import migrations, models

import sensors.models


def fill_tokens(apps, schema_editor):
    """Nadaje każdemu istniejącemu czujnikowi osobny klucz API."""
    Sensor = apps.get_model('sensors', 'Sensor')
    for sensor in Sensor.objects.all():
        sensor.api_token = secrets.token_hex(20)
        sensor.save(update_fields=['api_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('sensors', '0005_activity_login'),
    ]

    operations = [
        migrations.AddField(
            model_name='sensor',
            name='api_token',
            field=models.CharField(max_length=40, null=True, verbose_name='Klucz API'),
        ),
        migrations.RunPython(fill_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='sensor',
            name='api_token',
            field=models.CharField(
                default=sensors.models.generate_sensor_token, max_length=40, unique=True,
                verbose_name='Klucz API',
                help_text='Klucz, którym moduł pomiarowy uwierzytelnia wysyłane pomiary',
            ),
        ),
        migrations.RemoveField(
            model_name='usersettings',
            name='show_predictions',
        ),
    ]

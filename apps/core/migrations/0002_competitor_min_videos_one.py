"""
Lowers the competitor video-count floor from 20 to 1.

Only a value still at the old default is moved: a deployment where someone
already chose their own number under Settings keeps it.
"""

from django.db import migrations

KEY = "competitor_min_videos"
DESCRIPTION = "Fewest uploads a reference channel may have and still prove the niche."


def forwards(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    for row in SystemSetting.objects.filter(key=KEY):
        if str(row.value) == "20":
            row.value = 1
            row.description = DESCRIPTION
            row.save(update_fields=["value", "description"])


def backwards(apps, schema_editor):
    SystemSetting = apps.get_model("core", "SystemSetting")
    for row in SystemSetting.objects.filter(key=KEY):
        if str(row.value) == "1":
            row.value = 20
            row.save(update_fields=["value"])


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]

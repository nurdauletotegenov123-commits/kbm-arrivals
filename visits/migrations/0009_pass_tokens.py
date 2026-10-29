import secrets

from django.db import migrations, models

import visits.models


def fill_tokens(apps, schema_editor):
    """У каждого уже заведённого человека и заявки — свой код пропуска."""
    for name in ("Visitor", "Request"):
        model = apps.get_model("visits", name)
        for obj in model.objects.filter(pass_token__isnull=True).only("pk"):
            obj.pass_token = secrets.token_urlsafe(16)
            obj.save(update_fields=["pass_token"])


class Migration(migrations.Migration):
    dependencies = [("visits", "0008_rides")]

    operations = [
        migrations.AddField("visitor", "pass_token", models.CharField(max_length=32, null=True, editable=False)),
        migrations.AddField("request", "pass_token", models.CharField(max_length=32, null=True, editable=False)),
        migrations.RunPython(fill_tokens, migrations.RunPython.noop),
        migrations.AlterField("visitor", "pass_token", models.CharField(
            "код пропуска", max_length=32, unique=True, default=visits.models.new_pass_token, editable=False)),
        migrations.AlterField("request", "pass_token", models.CharField(
            "код общего пропуска", max_length=32, unique=True, default=visits.models.new_pass_token, editable=False)),
    ]

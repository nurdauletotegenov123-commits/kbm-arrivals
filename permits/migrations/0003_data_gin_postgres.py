from django.db import migrations


def create(apps, schema_editor):
    """GIN-индекс по data (как в ЭНД): видимость нарядов членам бригады за ~35 мс вместо ~4.5 с.
    Только PostgreSQL; на SQLite (ноутбуки) не нужен."""
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("CREATE INDEX IF NOT EXISTS workpermit_data_gin ON permits_workpermit "
                              "USING GIN (data jsonb_path_ops)")


def drop(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP INDEX IF EXISTS workpermit_data_gin")


class Migration(migrations.Migration):
    dependencies = [("permits", "0002_seed_work_types")]
    operations = [migrations.RunPython(create, drop)]

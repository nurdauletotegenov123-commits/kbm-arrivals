from django.db import migrations

DANGEROUS = [
    ("Огневые работы", "Отпен жұмыстар", "#E53935"),
    ("Газоопасные работы", "Газдан қауіпті жұмыстар", "#FB8C00"),
    ("Работы на высоте", "Биіктіктегі жұмыстар", "#1E88E5"),
    ("Земляные работы", "Жер жұмыстары", "#6D4C41"),
    ("Работы в замкнутом пространстве (ёмкости, колодцы, резервуары)", "Тұйық кеңістіктегі жұмыстар", "#8E24AA"),
    ("Работы в охранной зоне ЛЭП и подземных коммуникаций", "ЭБЖ күзет аймағындағы жұмыстар", "#FDD835"),
    ("Грузоподъёмные работы", "Жүк көтеру жұмыстары", "#00897B"),
    ("Работы на устье скважины и с давлением", "Ұңғыма сағасындағы жұмыстар", "#3949AB"),
]
ELECTRICAL = [
    ("Со снятием напряжения", "Кернеуді алып тастаумен", "#E53935"),
    ("Без снятия напряжения на токоведущих частях и вблизи них", "Кернеуді алмай", "#FB8C00"),
    ("Без снятия напряжения вдали от токоведущих частей", "Ток өткізгіш бөліктерден алыс", "#1E88E5"),
]


def seed(apps, schema_editor):
    """Стартовый справочник; администратор правит его в служебной админке."""
    for model, rows in (("DangerousWorkType", DANGEROUS), ("ElectricalWorkType", ELECTRICAL)):
        Model = apps.get_model("permits", model)
        for name, name_kk, color in rows:
            Model.objects.get_or_create(name=name, defaults={"name_kk": name_kk, "color_code": color})


class Migration(migrations.Migration):
    dependencies = [("permits", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

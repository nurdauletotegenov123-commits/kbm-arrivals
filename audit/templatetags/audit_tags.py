import re

from django import template

register = template.Library()


def _labels():
    from accounts.models import User
    from visits.models import Request, Visitor

    labels, values = {}, {}
    for model in (Visitor, Request, User):
        for f in model._meta.get_fields():
            if hasattr(f, "verbose_name"):
                labels.setdefault(f.name, str(f.verbose_name))
            for key, label in getattr(f, "choices", None) or []:
                values.setdefault(str(key), str(label))
    labels["organization_name"] = labels["organization"] = "организация"
    return labels, values


def _fmt(value, values):
    if value is True:
        return "да"
    if value is False:
        return "нет"
    if value in (None, ""):
        return "—"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "—"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    if m:
        return f"{m[3]}.{m[2]}.{m[1]}"
    return values.get(str(value), str(value))


@register.filter
def format_changes(changes):
    """{поле: [было, стало]} → ["поле: было → стало", ...]"""
    if not isinstance(changes, dict):
        return []
    labels, values = _labels()
    lines = []
    for key, value in changes.items():
        label = labels.get(key, key)
        if isinstance(value, list) and len(value) == 2 and key not in ("отменены",):
            lines.append(f"{label}: {_fmt(value[0], values)} → {_fmt(value[1], values)}")
        else:
            lines.append(f"{label}: {_fmt(value, values)}")
    return lines

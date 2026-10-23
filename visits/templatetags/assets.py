"""{% asset 'css/app.css' %} — адрес статического файла с номером версии.

После изменения файла адрес меняется, и браузеры (в том числе телефоны охраны)
сразу берут новую версию, а не старую из кэша.
"""
import os
from functools import lru_cache

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


def _mtime(path):
    found = finders.find(path)
    return int(os.path.getmtime(found)) if found else 0


_cached_mtime = lru_cache(maxsize=None)(_mtime)


@register.simple_tag
def asset(path):
    version = _mtime(path) if settings.DEBUG else _cached_mtime(path)
    return f"{static(path)}?v={version}"

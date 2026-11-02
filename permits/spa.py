"""Страница раздела «Наряд-допуски»: отдаёт собранный интерфейс нарядов (permits_ui → static/permits_ui)."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.views.decorators.cache import never_cache

INDEX = Path(settings.BASE_DIR) / "static" / "permits_ui" / "index.html"


@never_cache
@login_required
def permits_app(request):
    if not INDEX.exists():
        return HttpResponse("Интерфейс нарядов не собран: cd permits_ui && npm ci && npm run build", status=503,
                            content_type="text/plain; charset=utf-8")
    return HttpResponse(INDEX.read_text(encoding="utf-8"))

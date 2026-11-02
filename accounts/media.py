"""Файлы нарядов (сканы, подписи, фото LOTO) — только вошедшим на портал.
В ЭНД nginx отдавал их всем по прямой ссылке; здесь — через проверку входа.
Документы на груз (cargo/) сюда не попадают: у них своя проверка прав (visits.views.cargo_file)."""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404


@login_required
def protected_media(request, path):
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / path).resolve()
    if root not in target.parents or path.startswith("cargo/") or not target.is_file():
        raise Http404
    return FileResponse(target.open("rb"), content_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

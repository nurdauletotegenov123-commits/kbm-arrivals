import segno
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def qr_svg(url, scale=6):
    """QR-код как встроенный SVG: рисуется на сервере, без интернета и внешних сервисов."""
    code = segno.make(url, error="m")
    return mark_safe(code.svg_inline(scale=scale, border=2, dark="#182025", light="#ffffff"))

from django.db.models import Prefetch

from .models import Delivery, DeliveryItem

S = Delivery.Status


def with_items():
    return Delivery.objects.select_related("entered_by", "exited_by").prefetch_related(
        Prefetch("items", queryset=DeliveryItem.objects.order_by("pk")))


def on_day(qs, day):
    return qs.filter(date_from__lte=day, date_to__gte=day)


def for_kpp(today):
    """Поставки, которые охрана ждёт сегодня, и машины, что въехали и ещё не выехали."""
    return (on_day(with_items(), today).exclude(status=S.CANCELLED) | with_items().filter(status=S.ARRIVED)).distinct() \
        .order_by("exited_at", "entered_at", "date_from", "truck_plate")

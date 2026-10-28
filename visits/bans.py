"""Сверка людей и машин со списком запрета въезда.

Списки на экранах небольшие, поэтому все действующие запреты читаются одним запросом,
а совпадение проставляется атрибутом `ban` у каждого человека/машины.
"""
from .models import EntryBan


class BanIndex:
    def __init__(self):
        bans = list(EntryBan.objects.filter(is_active=True))
        self.by_doc = {b.doc_number: b for b in bans if b.doc_number}
        self.by_plate = {b.vehicle_plate: b for b in bans if b.vehicle_plate}

    def for_visitor(self, v):
        doc = (v.doc_number or "").replace(" ", "").upper()
        return self.by_doc.get(doc) or (self.by_plate.get(v.vehicle_plate) if v.vehicle_plate else None)

    def for_trip(self, t):
        return self.by_plate.get(t.vehicle_plate)

    def mark(self, visitors=(), trips=()):
        for v in visitors:
            v.ban = self.for_visitor(v)
        for t in trips:
            t.ban = self.for_trip(t)


def mark(visitors=(), trips=()):
    """Проставить `ban` людям и машинам. Возвращает число совпадений."""
    visitors, trips = list(visitors), list(trips)
    if not visitors and not trips:
        return 0
    BanIndex().mark(visitors, trips)
    return sum(1 for x in visitors + trips if x.ban)

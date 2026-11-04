"""Разбор ежедневного плана поставок (Excel снабжения) и загрузка его в систему.

Файл ведут вручную, поэтому разбор терпимый к беспорядку:
- колонки ищутся по словам в шапке (шапка бывает в две строки: «Кон. данные ответ.лиц» → «Ф.И.О водителя»);
- объединённые ячейки разворачиваются (одна машина на несколько строк груза);
- строки «Планируемая поставка на 01.11.2026» задают день, к которому относятся строки ниже;
- даты: «31.10-04.11.2026», «31.10.-01.11.2026.», «01.11.2026г. -05.11.2026г.», «02-03.11.2026»,
  «31.10.2026 по 01.11.2026», «10/31/2026» (Excel в американском формате), «03/11/2026-06/11/2026», ячейка-дата;
- госномера: «046LEX15 40PAZ15», «808XXA17/ прицеп KRONE SD 97AMH17», «KZ 585AFU 12», «202 AKK 12»,
  «797AOJ/01», «гос. номер 565AAG04, прицеп гос. номер 14AAO04».
Одна машина в плане повторяется по дням — при загрузке она не дублируется, а обновляется.
"""
import re
from datetime import date, datetime

from django.db import transaction
from openpyxl import load_workbook

from visits.text import normalize_plate

from .models import Delivery, DeliveryItem

# поле → слова в шапке (порядок важен: более точные раньше)
COLUMNS = [
    ("abp", [r"^абп\b", r"\bабп\b"]),
    ("supplier", [r"подрядн", r"поставщик"]),
    ("name", [r"наименован"]),
    ("quantity", [r"объ[её]м", r"кол-?во", r"количеств"]),
    ("contract", [r"договор"]),
    ("vehicle", [r"вид транспорт", r"марка"]),
    ("plate", [r"номер авто", r"гос\.?\s*номер", r"госномер"]),
    ("dates", [r"дата въезда", r"дата"]),
    ("driver", [r"водител"]),
    ("phone", [r"телефон", r"\bтел\b"]),
    ("contact", [r"ответ", r"контакт"]),
    ("departure", [r"пункт отправ"]),
    ("work_type", [r"вид работ"]),
    ("note", [r"примечан"]),
    ("otp", [r"\bотп\b"]),
    ("final", [r"финальн"]),
    ("local_content", [r"местного содерж", r"ст-?\s*kz", r"ст-?\s*кз"]),
]
SECTION = re.compile(r"планируем\w*\s+поставк\w*\s+на\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4})", re.I)


class PlanError(Exception):
    """Файл не похож на план поставок."""


def _text(v):
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d.%m.%Y")
    return " ".join(str(v).split())


def _grid(ws):
    """Значения листа с развёрнутыми объединёнными ячейками."""
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    for rng in ws.merged_cells.ranges:
        value = ws.cell(rng.min_row, rng.min_col).value
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                if r - 1 < len(rows) and c - 1 < len(rows[r - 1]):
                    rows[r - 1][c - 1] = value
    return rows


def _map_columns(rows):
    """(номер строки, где кончается шапка, {поле: колонка}). Шапка — строка со словами «наименование» и «договор»/«подрядная»."""
    for i, row in enumerate(rows[:30]):
        line = " ".join(_text(v).lower() for v in row)
        if "наименован" in line and ("договор" in line or "подрядн" in line or "абп" in line):
            # шапка бывает в 2 строки («Кон. данные ответ.лиц» → «Ф.И.О водителя», «телефон»)
            end = i + 1
            while end < min(len(rows), i + 3):
                nxt = " ".join(_text(v).lower() for v in rows[end])
                if not re.search(r"водител|телефон|ф\.?\s*и\.?\s*о", nxt) or SECTION.search(nxt):
                    break
                end += 1
            width = max(len(r) for r in rows[i:end])
            headers = [" ".join(_text(rows[r][c]).lower() for r in range(i, end) if c < len(rows[r])) for c in range(width)]
            mapping, used = {}, set()
            for field, patterns in COLUMNS:
                for c, h in enumerate(headers):
                    if c not in used and any(re.search(p, h) for p in patterns):
                        mapping[field] = c
                        used.add(c)
                        break
            if "name" not in mapping:
                break
            return end, mapping
    raise PlanError("Не нашли шапку таблицы: нужна строка с колонками «Наименование…» и «№ договора» или «Подрядная организация».")


# ---------- даты ----------

_TOKEN = re.compile(r"(\d{1,2})([./])(\d{1,2})(?:\2(\d{2,4}))?")


def _year(y, fallback):
    if not y:
        return fallback
    y = int(y)
    return y + 2000 if y < 100 else y


def _candidates(a, sep, b, y):
    a, b = int(a), int(b)
    out = []
    if sep == ".":
        out.append((y, b, a))  # день.месяц
    else:  # «/» — Excel пишет и 10/31/2026 (м/д), и люди 03/11/2026 (д/м)
        if a > 12:
            out.append((y, b, a))
        elif b > 12:
            out.append((y, a, b))
        else:
            out += [(y, b, a), (y, a, b)]
    res = []
    for yy, m, d in out:
        try:
            res.append(date(yy, m, d))
        except ValueError:
            pass
    return res


def parse_dates(value, plan_date=None):
    """(с, по) или None. plan_date помогает с годом и с выбором между д/м и м/д."""
    if isinstance(value, datetime):
        return value.date(), value.date()
    if isinstance(value, date):
        return value, value
    text = _text(value)
    if not text:
        return None
    base_year = plan_date.year if plan_date else date.today().year
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", text)]
    default_year = years[-1] if years else base_year
    # «02-03.11.2026» — диапазон дней внутри одного месяца
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text)
    if m and (m.start(1) == 0 or text[m.start(1) - 1] not in "./"):  # «30.10-01.11» — не диапазон дней
        y, mo = _year(m.group(4), default_year), int(m.group(3))
        try:
            return date(y, mo, int(m.group(1))), date(y, mo, int(m.group(2)))
        except ValueError:
            pass
    found = []
    for t in _TOKEN.finditer(text):
        options = _candidates(t.group(1), t.group(2), t.group(3), _year(t.group(4), default_year))
        if not options:
            continue
        if plan_date and len(options) > 1:
            options.sort(key=lambda d: abs((d - plan_date).days))
        found.append(options[0])
    if not found:
        return None
    return min(found), max(found)


# ---------- госномера ----------

_PLATE = re.compile(r"\b(\d{3}[A-Z]{2,3}\d{2}|\d{2}[A-Z]{3}\d{2}|[A-Z]\d{6})\b")


def parse_plates(*texts):
    """(машина, прицеп) из текста колонки номера (а если там пусто — из «вида транспорта»)."""
    for text in texts:
        s = _text(text).upper().translate(str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY"))
        s = re.sub(r"\bKZ\b", " ", s)
        s = re.sub(r"([A-Z]{3})/(\d{2})\b", r"\1\2", s)          # 797AOJ/01
        s = re.sub(r"(?<=\d)\s+(?=[A-Z]{2,3}\b)", "", s)          # 202 AKK 12 → 202AKK 12
        s = re.sub(r"(?<=\d[A-Z]{2})\s+(?=\d{2}\b)", "", s)       # 202AK 12
        s = re.sub(r"(?<=\d[A-Z]{3})\s+(?=\d{2}\b)", "", s)       # 202AKK 12 → 202AKK12
        found = _PLATE.findall(s)
        if found:
            return found[0], (found[1] if len(found) > 1 else "")
    return "", ""


def _flag(value):
    t = _text(value).lower()
    if t.startswith("да"):
        return True
    if t.startswith("нет"):
        return False
    return None


def _qty_from_name(name):
    m = re.search(r"[-–—]\s*(\d[\d\s,.]*\s*(?:шт|м|т|кг|компл|ед)\.?)\s*$", name, re.I)
    return m.group(1).strip() if m else ""


def parse_plan(file):
    """→ (поставки, предупреждения). Поставка: dict с машиной, датами и списком позиций."""
    try:
        wb = load_workbook(file, data_only=True)
    except Exception as e:  # noqa: BLE001 — показываем пользователю понятную причину
        raise PlanError(f"Не удалось открыть файл как Excel (.xlsx): {e}")
    last_error = None
    for ws in wb.worksheets:
        rows = _grid(ws)
        try:
            start, cols = _map_columns(rows)
        except PlanError as e:
            last_error = e
            continue
        return _parse_rows(rows[start:], cols, start)
    raise last_error or PlanError("В файле нет листов.")


def _parse_rows(rows, cols, offset):
    get = lambda row, field: row[cols[field]] if field in cols and cols[field] < len(row) else None  # noqa: E731
    plan_date, groups, warnings = None, {}, []
    for n, row in enumerate(rows, start=offset + 1):
        line = " ".join(_text(v) for v in row)
        sec = SECTION.search(line)
        if sec:
            d = parse_dates(sec.group(1))
            plan_date = d[0] if d else plan_date
            continue
        name = _text(get(row, "name"))
        if not name:
            continue
        plate_text, vehicle = _text(get(row, "plate")), _text(get(row, "vehicle"))
        truck, trailer = parse_plates(plate_text, vehicle)
        dates = parse_dates(get(row, "dates"), plan_date) or ((plan_date, plan_date) if plan_date else None)
        if not dates:
            warnings.append(f"Строка {n}: нет даты въезда — пропущена ({name[:50]}).")
            continue
        if not truck and not plate_text:
            warnings.append(f"Строка {n}: не указан госномер ({name[:50]}).")
        key = (truck or plate_text or _text(get(row, "driver")) or f"row{n}", dates[0], dates[1])
        g = groups.setdefault(key, {
            "date_from": dates[0].isoformat(), "date_to": dates[1].isoformat(),
            "vehicle": vehicle[:200], "truck_plate": truck, "trailer_plate": trailer, "plate_raw": plate_text[:200],
            "driver_name": _text(get(row, "driver"))[:200], "contact_name": "", "contact_phone": "",
            "departure_point": _text(get(row, "departure"))[:200], "work_type": _text(get(row, "work_type"))[:200],
            "note": _text(get(row, "note"))[:300], "items": [],
        })
        item = {
            "abp": _text(get(row, "abp"))[:50], "supplier": _text(get(row, "supplier"))[:300], "name": name,
            "quantity": (_text(get(row, "quantity")) or _qty_from_name(name))[:100],
            "contract": _text(get(row, "contract"))[:300],
            "is_otp": _flag(get(row, "otp")), "is_final": _flag(get(row, "final")),
            "local_content_ok": _flag(get(row, "local_content")),
        }
        if not any(i["name"] == item["name"] and i["contract"] == item["contract"] for i in g["items"]):
            g["items"].append(item)
        # данные машины могли быть заполнены не в первой строке её груза
        phone, contact = _text(get(row, "phone")), _text(get(row, "contact"))
        if phone and len(re.sub(r"\D", "", phone)) < 7:  # в колонке «телефон» вместо номера ФИО
            contact, phone = contact or phone, ""
        g["contact_phone"] = g["contact_phone"] or phone[:200]
        g["contact_name"] = g["contact_name"] or (contact if contact != g["driver_name"] else "")[:200]
        for field, column in (("driver_name", "driver"), ("departure_point", "departure"),
                              ("work_type", "work_type"), ("note", "note"), ("vehicle", "vehicle")):
            if not g[field]:
                g[field] = _text(get(row, column))[:200]
    if not groups:
        raise PlanError("В таблице не нашлось ни одной строки с наименованием груза.")
    return list(groups.values()), warnings


def _arrived_note(note):
    t = note.lower().replace("ё", "е")
    return "прибыл" in t and "неприбыл" not in t and "не прибыл" not in t


@transaction.atomic
def apply_plan(deliveries, user):
    """Загрузить разобранный план. Та же машина с теми же датами обновляется, а не дублируется.
    Возвращает {"created", "updated", "items"}."""
    stats = {"created": 0, "updated": 0, "items": 0}
    for d in deliveries:
        lookup = {"date_from": d["date_from"], "date_to": d["date_to"]}
        lookup.update({"truck_plate": normalize_plate(d["truck_plate"])} if d["truck_plate"] else {"plate_raw": d["plate_raw"]})
        delivery = Delivery.objects.filter(**lookup).exclude(status=Delivery.Status.CANCELLED).first()
        fields = {k: d.get(k, "") for k in ("vehicle", "trailer_plate", "plate_raw", "driver_name", "contact_name", "contact_phone",
                                     "departure_point", "work_type", "note")}
        if delivery is None:
            delivery = Delivery(source=Delivery.Source.EXCEL, created_by=user, truck_plate=d["truck_plate"],
                                date_from=d["date_from"], date_to=d["date_to"], **fields)
            stats["created"] += 1
        else:
            for k, v in fields.items():
                if v:
                    setattr(delivery, k, v)
            stats["updated"] += 1
        if _arrived_note(d["note"]) and not delivery.is_marked:
            delivery.marked_arrived_in_plan = True
        delivery.save()
        existing = {(i.name, i.contract): i for i in delivery.items.all()}
        for it in d["items"]:
            item = existing.get((it["name"], it["contract"]))
            if item is None:
                DeliveryItem.objects.create(delivery=delivery, **it)
                stats["items"] += 1
            else:
                for k, v in it.items():
                    if v not in ("", None):
                        setattr(item, k, v)
                item.save()
        delivery.reindex()
    return stats

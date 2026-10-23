"""Импорт списка людей из Excel и выгрузка отчётов."""
import re
from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import Visitor

MAX_ROWS = 500

# Поле формы → возможные названия колонки (в нижнем регистре).
COLUMNS = {
    "full_name": ["фио", "ф.и.о.", "ф.и.о", "фамилия имя отчество", "фамилия, имя, отчество", "сотрудник"],
    "doc_type": ["тип документа", "документ (тип)"],
    "doc_number": ["иин", "иин / номер документа", "иин/номер документа", "номер документа", "иин или паспорт",
                   "иин / паспорт", "иин/паспорт", "№ документа", "удостоверение", "паспорт"],
    "organization_name": ["организация", "компания", "предприятие"],
    "position": ["должность", "профессия"],
    "arrival_date": ["дата прибытия", "дата заезда", "прибытие"],
    "arrival_time": ["время прибытия", "время"],
    "departure_date": ["дата выезда", "дата убытия", "выезд"],
    "vehicle_make": ["марка авто", "марка", "транспорт", "автомобиль"],
    "vehicle_plate": ["госномер", "гос. номер", "гос номер", "номер авто", "гос.номер"],
    "needs_transport": ["нужен транспорт", "транспорт диспетчера", "нужна машина"],
    "needs_camp": ["проживание", "кэмп", "проживание в кэмпе"],
    "breakfast": ["завтрак"],
    "lunch": ["обед"],
    "dinner": ["ужин"],
}
_HEADER_LOOKUP = {alias: field for field, aliases in COLUMNS.items() for alias in aliases}
TEMPLATE_HEADERS = [
    ("ФИО", 34), ("Тип документа", 16), ("ИИН / номер документа", 22), ("Организация", 28), ("Должность", 22),
    ("Дата прибытия", 14), ("Время прибытия", 14), ("Дата выезда", 14), ("Марка авто", 16), ("Госномер", 14),
    ("Проживание", 12), ("Завтрак", 10), ("Обед", 10), ("Ужин", 10), ("Нужен транспорт", 16),
]
TRUE_VALUES = {"да", "+", "1", "x", "х", "v", "true", "yes", "иә", "ия", "✓", "✔"}
HEADER_FILL = PatternFill("solid", fgColor="D2ECF8")  # светлый фирменный голубой
BOLD = Font(bold=True)


def _norm_header(value):
    return " ".join(str(value or "").lower().replace("*", "").replace("ё", "е").split())


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _time(value):
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, float) and 0 <= value < 1:  # доля суток
        minutes = round(value * 24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})", str(value or ""))
    return f"{int(m[1]):02d}:{m[2]}" if m else ""


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _doc_type(raw, number):
    raw = raw.lower()
    if "паспорт" in raw or "passport" in raw:
        return Visitor.DocType.PASSPORT
    if "удостовер" in raw:
        return Visitor.DocType.ID_CARD
    if "иин" in raw:
        return Visitor.DocType.IIN
    return Visitor.DocType.IIN if re.fullmatch(r"\d{10,12}", number) else Visitor.DocType.PASSPORT


def parse_people(file):
    """Прочитать список людей. Возвращает (rows, errors); rows — словари для полей формы."""
    try:
        wb = load_workbook(file, read_only=True, data_only=True)
    except Exception:
        return [], ["Не удалось открыть файл. Сохраните его в формате .xlsx и попробуйте снова."]
    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)

    mapping = None
    for _ in range(10):  # шапка — в одной из первых 10 строк
        try:
            header = next(rows_iter)
        except StopIteration:
            break
        candidate = {i: _HEADER_LOOKUP[_norm_header(h)] for i, h in enumerate(header) if _norm_header(h) in _HEADER_LOOKUP}
        if "full_name" in candidate.values():
            mapping = candidate
            break
    if not mapping:
        return [], ["Не найдена колонка «ФИО». Скачайте шаблон и заполните его."]

    rows, errors = [], []
    for line_no, values in enumerate(rows_iter, start=2):
        raw = {field: values[i] if i < len(values) else None for i, field in mapping.items()}
        if not any(_text(v) for v in raw.values()):
            continue
        if len(rows) >= MAX_ROWS:
            errors.append(f"Загружены только первые {MAX_ROWS} строк.")
            break
        row = {}
        for field, value in raw.items():
            if field in ("arrival_date", "departure_date"):
                row[field] = _date(value)
            elif field == "arrival_time":
                row[field] = _time(value)
            elif field in ("needs_transport", "needs_camp", "breakfast", "lunch", "dinner"):
                row[field] = _text(value).lower() in TRUE_VALUES
            else:
                row[field] = _text(value)
        number = row.get("doc_number", "").replace(" ", "")
        row["doc_type"] = _doc_type(row.get("doc_type", ""), number)
        if row["doc_type"] == Visitor.DocType.IIN and number.isdigit() and len(number) < 12:
            number = number.zfill(12)  # Excel съедает ведущие нули ИИН
        row["doc_number"] = number
        if any(row.get(m) for m in ("breakfast", "lunch", "dinner")):
            row["needs_meals"] = True
        if not row.get("full_name"):
            errors.append(f"Строка {line_no}: не указано ФИО — строка пропущена.")
            continue
        rows.append(row)
    if not rows and not errors:
        errors.append("В файле нет строк с людьми.")
    return rows, errors


# --- выгрузка ---


def _sheet(ws, headers, rows, widths=None):
    ws.append(headers)
    for cell in ws[1]:
        cell.font, cell.fill = BOLD, HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rows:
        ws.append(row)
    for i, h in enumerate(headers, start=1):
        width = (widths or {}).get(h) or max(10, min(40, len(str(h)) + 4))
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _xlsx_response(wb, filename):
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _dt(value):
    return timezone.localtime(value).replace(tzinfo=None) if value else None


def import_template():
    wb = Workbook()
    ws = wb.active
    ws.title = "Список людей"
    headers = [h for h, _ in TEMPLATE_HEADERS]
    today = timezone.localdate()
    _sheet(ws, headers, [
        ["Иванов Иван Иванович", "ИИН", "900101300123", "ТОО «Пример»", "Слесарь", today + timedelta(days=1),
         "09:00", today + timedelta(days=8), "Toyota Hilux", "123ABC12", "да", "да", "да", "да", ""],
        ["Smith John", "Паспорт", "N12345678", "ТОО «Пример»", "Инженер", today + timedelta(days=1),
         "", today + timedelta(days=1), "", "", "", "", "да", "", "да"],
    ], dict(TEMPLATE_HEADERS))
    for col in ("C",):
        for cell in ws[col]:
            cell.number_format = "@"
    for col in ("F", "H"):
        for cell in ws[col][1:]:
            cell.number_format = "DD.MM.YYYY"
    ws["Q1"] = "Колонки, кроме ФИО, необязательны. Даты, время и галочки, если не заполнены, берутся из формы заявки."
    return _xlsx_response(wb, "shablon_spiska_lyudey.xlsx")


def _period_name(prefix, d1, d2):
    return f"{prefix}_{d1:%Y%m%d}-{d2:%Y%m%d}.xlsx"


def report_movements(visitors, d1, d2, show_docs):
    wb = Workbook()
    ws = wb.active
    ws.title = "Прибытие и убытие"
    headers = ["Заявка №", "ФИО"] + (["Документ", "Номер документа"] if show_docs else []) + [
        "Организация", "Должность", "Цель визита", "План: прибытие", "План: выезд", "Статус",
        "Прибыл (факт)", "Отметил прибытие", "КПП прибытия", "Убыл (факт)", "Отметил убытие", "КПП убытия",
        "Госномер", "Марка авто", "Ответственный", "Телефон", "Не прибыл в срок",
    ]
    rows = []
    for v in visitors:
        rows.append([v.request.number, v.full_name] + ([v.get_doc_type_display(), v.doc_number] if show_docs else []) + [
            str(v.organization), v.position, v.request.purpose, v.arrival_date, v.departure_date,
            v.get_status_display(), _dt(v.arrived_at), str(v.arrived_by or ""), str(v.arrived_checkpoint or ""),
            _dt(v.departed_at), str(v.departed_by or ""), str(v.departed_checkpoint or ""),
            v.vehicle_plate, v.vehicle_make, v.request.contact_name, v.request.contact_phone,
            "да" if v.is_no_show else "",
        ])
    _sheet(ws, headers, rows, {"ФИО": 32, "Организация": 26, "Цель визита": 30, "Прибыл (факт)": 17, "Убыл (факт)": 17})
    _format_dates(ws)
    return _xlsx_response(wb, _period_name("pribytie", d1, d2))


def meal_rows(visitors, d1, d2):
    """[(день, завтраки, обеды, ужины, людей, [люди])] за период."""
    result = []
    day = d1
    while day <= d2:
        people = [v for v in visitors if v.eats_on(day)]
        result.append((
            day,
            sum(v.breakfast for v in people),
            sum(v.lunch for v in people),
            sum(v.dinner for v in people),
            len(people),
            people,
        ))
        day += timedelta(days=1)
    return result


def report_meals(visitors, d1, d2):
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    data = meal_rows(visitors, d1, d2)
    _sheet(ws, ["Дата", "Завтрак", "Обед", "Ужин", "Всего порций", "Человек"],
           [[d, b, l, dn, b + l + dn, n] for d, b, l, dn, n, _ in data])
    ws.append(["Итого", *[f"=SUM({c}2:{c}{len(data) + 1})" for c in "BCDEF"]])
    for cell in ws[ws.max_row]:
        cell.font = BOLD
    _format_dates(ws)

    ws2 = wb.create_sheet("Люди по дням")
    rows = []
    for d, *_, people in data:
        for v in people:
            rows.append([d, v.full_name, str(v.organization), v.position, "да" if v.breakfast else "",
                         "да" if v.lunch else "", "да" if v.dinner else "", v.get_status_display(),
                         "не прибыл" if v.is_no_show else ""])
    _sheet(ws2, ["Дата", "ФИО", "Организация", "Должность", "Завтрак", "Обед", "Ужин", "Статус КПП", "Отметка"], rows,
           {"ФИО": 32, "Организация": 26})
    _format_dates(ws2)
    return _xlsx_response(wb, _period_name("pitanie", d1, d2))


def report_camp(visitors, d1, d2):
    wb = Workbook()
    ws = wb.active
    ws.title = "Проживание"
    rows = [[v.request.number, v.full_name, str(v.organization), v.position, v.arrival_date, v.departure_date,
             v.nights, v.camp_building, v.camp_room, v.get_camp_status_display(), _dt(v.checked_in_at),
             _dt(v.checked_out_at), v.get_status_display(), "да" if v.is_no_show else ""] for v in visitors]
    _sheet(ws, ["Заявка №", "ФИО", "Организация", "Должность", "План: заезд", "План: выезд", "Ночей",
                "Корпус", "Комната", "Статус кэмпа", "Заселён", "Выселен", "Статус КПП", "Не прибыл в срок"], rows,
           {"ФИО": 32, "Организация": 26, "Заселён": 17, "Выселен": 17})
    _format_dates(ws)

    ws2 = wb.create_sheet("По дням")
    day, rows = d1, []
    while day <= d2:
        rows.append([day,
                     sum(1 for v in visitors if v.arrival_date == day),
                     sum(1 for v in visitors if v.departure_date == day),
                     sum(1 for v in visitors if v.arrival_date <= day < v.departure_date)])
        day += timedelta(days=1)
    _sheet(ws2, ["Дата", "Заезд (план)", "Выезд (план)", "Ночуют (план)"], rows)
    _format_dates(ws2)
    return _xlsx_response(wb, _period_name("kemp", d1, d2))


def _format_dates(ws):
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, datetime):
                cell.number_format = "DD.MM.YYYY HH:MM"
            elif isinstance(cell.value, date):
                cell.number_format = "DD.MM.YYYY"

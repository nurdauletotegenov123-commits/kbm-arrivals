"""Нормализация текста для поиска и госномеров."""
import re

# Кириллические буквы, которые на номерах выглядят как латинские.
_CYR_TO_LAT = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


def normalize_plate(value):
    """«123 авс 12» → «123ABC12». Охрана может набирать номер кириллицей."""
    return re.sub(r"[\s\-]", "", (value or "").upper()).translate(_CYR_TO_LAT)


def normalize_search(value):
    return " ".join((value or "").lower().replace("ё", "е").split())


def search_terms(query):
    """Варианты строки поиска: как есть и как госномер."""
    terms = {normalize_search(query)}
    plate = normalize_plate(query).lower()
    if plate:
        terms.add(plate)
    return [t for t in terms if t]

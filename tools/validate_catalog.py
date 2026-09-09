#!/usr/bin/env python3
"""Проверка catalog/catalog_drugs.csv перед тем, как он уедет на телефоны.

Приложение разбирает файл снисходительно: битую строку оно молча пропускает,
чтобы одна опечатка не оставила человека без подсказок. Здесь наоборот —
про каждую такую строку нужно узнать до пуша, а не по жалобе пользователя.

Запуск:  python tools/validate_catalog.py
"""

import sys
from pathlib import Path

FORMS = {
    "TABLET", "CAPSULE", "DROPS", "SYRUP", "SUSPENSION", "INJECTION",
    "INHALER", "SPRAY", "OINTMENT", "SUPPOSITORY", "PATCH", "POWDER", "OTHER",
}
UNITS = {
    "PIECE", "MG", "G", "ML", "DROP", "PUFF", "IU", "SACHET",
    "APPLICATION", "OTHER",
}
# ANY здесь намеренно нет: «не важно» записывается пустым полем, а не словом,
# иначе в файле не отличить «указания нет» от «указание есть, и оно любое».
FOODS = {"EMPTY_STOMACH", "BEFORE_MEAL", "WITH_MEAL", "AFTER_MEAL"}

# Столько же, сколько CatalogRepository.MIN_VALID_ENTRIES: меньше — приложение
# сочтёт файл испорченным и не станет заменять уже загруженный.
MIN_ENTRIES = 20

CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "catalog_drugs.csv"


def main() -> int:
    if not CATALOG.exists():
        print(f"нет файла {CATALOG}")
        return 1

    raw = CATALOG.read_bytes()
    text = raw.decode("utf-8")

    errors: list[str] = []
    warnings: list[str] = []

    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append("файл начинается с BOM — сохраните как UTF-8 без BOM")
    if b"\r\n" in raw:
        warnings.append("переводы строк CRLF; ожидается LF")

    version = ""
    names: dict[str, int] = {}
    count = 0

    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue
        if line.startswith("#"):
            if not version and "version=" in line:
                version = line.split("version=", 1)[1].split(" ")[0].strip()
            continue
        if line.startswith("name;"):
            continue

        parts = line.split(";")
        if len(parts) < 3:
            errors.append(f"строка {number}: меньше трёх колонок — {line!r}")
            continue
        if len(parts) > 6:
            errors.append(f"строка {number}: лишняя «;» в тексте — {line!r}")
            continue

        name = parts[0].strip()
        if not name:
            errors.append(f"строка {number}: пустое наименование")
            continue

        key = name.lower()
        if key in names:
            warnings.append(f"строка {number}: «{name}» уже есть в строке {names[key]}")
        else:
            names[key] = number

        form = parts[2].strip()
        if form and form not in FORMS:
            errors.append(f"строка {number}: неизвестная форма «{form}»")

        strength = parts[3].strip() if len(parts) > 3 else ""
        if strength:
            try:
                float(strength.replace(",", "."))
            except ValueError:
                errors.append(f"строка {number}: дозировка «{strength}» не число")

        unit = parts[4].strip() if len(parts) > 4 else ""
        if unit and unit not in UNITS:
            errors.append(f"строка {number}: неизвестная единица «{unit}»")
        if strength and not unit:
            warnings.append(f"строка {number}: дозировка без единицы измерения")

        food = parts[5].strip() if len(parts) > 5 else ""
        if food and food not in FOODS:
            errors.append(f"строка {number}: недопустимая связь с едой «{food}»")

        count += 1

    if not version:
        errors.append("в шапке нет «version=…» — приложению нечего показать в настройках")
    if count < MIN_ENTRIES:
        errors.append(f"записей {count}, приложение примет файл начиная с {MIN_ENTRIES}")

    for text_line in warnings:
        print(f"предупреждение: {text_line}")
    for text_line in errors:
        print(f"ОШИБКА: {text_line}")

    print(f"\nверсия {version or '—'}, записей {count}, "
          f"ошибок {len(errors)}, предупреждений {len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

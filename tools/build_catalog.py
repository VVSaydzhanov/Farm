#!/usr/bin/env python3
"""Собирает catalog/catalog_drugs.csv из исходников в source/.

Зачем отдельная сборка, а не правка итогового файла руками: связь с едой
задаётся один раз на действующее вещество, а торговых наименований у одного
вещества десяток. Держать это в итоговом файле — гарантированно получить
«Нурофен после еды», а «Миг 400» без указания.

Запуск:  python tools/build_catalog.py
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source"
OUT = ROOT / "catalog" / "catalog_drugs.csv"

FOODS = {"EMPTY_STOMACH", "BEFORE_MEAL", "WITH_MEAL", "AFTER_MEAL"}

HEADER = """# pillbox-catalog version={version} count={count}
#
# Справочник для подсказок при вводе названия лекарства.
# Собирается из source/ скриптом tools/build_catalog.py — правьте исходники,
# а не этот файл: он перезаписывается целиком.
#
# ВАЖНО: список составлен вручную по распространённым препаратам и НЕ является
# медицинским справочником. Возможны неточности в дозировках и формах выпуска.
# Пользователь всегда видит подставленное значение и может его исправить.
#
# Колонка food — общее указание из инструкции производителя, одинаковое для
# всех наименований с этим действующим веществом. ЭТО НЕ НАЗНАЧЕНИЕ: врач
# может назначить иначе, и его назначение важнее. Приложение показывает,
# откуда взято значение, и позволяет его изменить.
#
# Хранятся только фактические сведения с упаковки: торговое наименование,
# действующее вещество, форма выпуска, дозировка и связь с едой. Описания,
# показания и инструкции защищены авторским правом и сюда попадать не должны.
#
# Формат: название;действующее вещество;форма;дозировка;единица;связь с едой
# Форма, единица и связь с едой — имена констант MedicationForm, DoseUnit
# и FoodRelation. Пустое значение означает «не указано».
name;inn;form;strength;unit;food
"""


def read_rows(path: Path) -> list[list[str]]:
    rows = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        if not parts[0]:
            continue
        if len(parts) < 2:
            print(f"{path.name}:{number}: нет действующего вещества — {line!r}")
            continue
        rows.append((parts + ["", "", "", ""])[:5])
    return rows


def read_food(path: Path) -> dict[str, str]:
    mapping = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2:
            print(f"{path.name}:{number}: нет значения — {line!r}")
            continue
        inn, food = parts[0], parts[1]
        if food not in FOODS:
            print(f"{path.name}:{number}: недопустимое значение «{food}»")
            continue
        mapping[inn.lower()] = food
    return mapping


def main() -> int:
    rows = read_rows(SOURCE / "drugs.tsv") + read_rows(SOURCE / "drugs_add.tsv")
    food = read_food(SOURCE / "food_by_inn.tsv")

    # Дубли по названию: одно и то же наименование могло попасть в оба файла.
    seen: dict[str, list[str]] = {}
    for row in rows:
        seen.setdefault(row[0].lower(), row)

    lines, matched = [], 0
    for row in seen.values():
        name, inn, form, strength, unit = row
        relation = food.get(inn.lower(), "")
        if relation:
            matched += 1
        lines.append(";".join([name, inn, form, strength, unit, relation]))

    lines.sort(key=lambda s: s.lower())

    version = date.today().isoformat()
    OUT.write_text(
        HEADER.format(version=version, count=len(lines)) + "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    unused = sorted(set(food) - {r[1].lower() for r in seen.values()})

    print(f"версия {version}")
    print(f"записей {len(lines)}, из них с указанием по еде {matched}")
    print(f"веществ в таблице еды {len(food)}")
    if unused:
        print("\nвещества из food_by_inn.tsv, которых нет в списке "
              f"({len(unused)}): {', '.join(unused)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

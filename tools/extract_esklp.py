#!/usr/bin/env python3
"""Извлекает таблицу «штрихкод — препарат» из выгрузки ЕСКЛП.

ЕСКЛП — единый структурированный справочник-каталог лекарственных препаратов
Минздрава. Из него берутся только фактические сведения с упаковки: штрихкод,
торговое наименование, действующее вещество, форма и дозировка. Описаний,
показаний и прочего защищённого авторским правом содержимого в нём нет
и сюда не попадает.

Важное ограничение: штрихкоды в ЕСКЛП лежат в блоке предельных отпускных цен,
а он заполняется только для ЖНВЛП. Препараты вне этого перечня и тем более
БАДы штрихкодов в выгрузке не имеют — их код с упаковки не опознается.

Где взять исходник:
    https://esklp.egisz.rosminzdrav.ru → Справочник ЕСКЛП в формате XML
    → esklp_ГГГГММДД_active_*.xml (полная выгрузка активных записей)

Запуск:
    python tools/extract_esklp.py путь/к/esklp_..._active_....xml
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "gtin.csv"

# Лекарственная форма ЕСКЛП -> константа MedicationForm.
# Слева подстрока, поиск идёт по порядку: более узкие варианты выше.
FORMS = [
    ("КАПСУЛ", "CAPSULE"),
    ("ТАБЛЕТК", "TABLET"),
    ("ДРАЖЕ", "TABLET"),
    ("ГРАНУЛ", "POWDER"),
    ("ПОРОШОК", "POWDER"),
    ("ЛИОФИЛИЗАТ", "POWDER"),
    ("СУППОЗИТОРИ", "SUPPOSITORY"),
    ("ПЛАСТЫРЬ", "PATCH"),
    ("СИРОП", "SYRUP"),
    ("СУСПЕНЗИ", "SUSPENSION"),
    ("ЭМУЛЬСИ", "SUSPENSION"),
    ("КАПЛИ", "DROPS"),
    ("СПРЕЙ", "SPRAY"),
    ("АЭРОЗОЛЬ", "INHALER"),
    ("ИНГАЛЯЦ", "INHALER"),
    ("МАЗЬ", "OINTMENT"),
    ("КРЕМ", "OINTMENT"),
    ("ГЕЛЬ", "OINTMENT"),
    ("ЛИНИМЕНТ", "OINTMENT"),
    ("ПАСТА", "OINTMENT"),
    ("ИНЪЕКЦ", "INJECTION"),
    ("ИНФУЗ", "INJECTION"),
    ("РАСТВОР ДЛЯ ВНУТРИ", "INJECTION"),
    # Раствор для приёма внутрь — это флакон с мерным стаканчиком,
    # а не капли: «КАПЛИ» проверяются выше и сюда не доходят.
    ("РАСТВОР ДЛЯ ПРИЕМА ВНУТРЬ", "SYRUP"),
    ("РАСТВОР", "INJECTION"),
]

# Единица дозировки ЕСКЛП -> константа DoseUnit.
UNITS = [
    ("МЛН МЕ", None),        # слишком редко и неоднозначно, пропускаем
    ("МЕ", "IU"),
    ("МКГ", None),           # микрограммов в DoseUnit нет
    ("МГ", "MG"),
    ("Г", "G"),
    ("МЛ", "ML"),
]

KLP = re.compile(r"<ns2:klp\b.*?</ns2:klp>", re.S)
BARCODE = re.compile(r"<ns2:barcode>(\d{8,14})</ns2:barcode>")


def pretty(text: str) -> str:
    """Смягчить верхний регистр, которым в ЕСКЛП записана часть названий.

    В выгрузке соседствуют «Парацетамол» и «АЦИКЛОВИР ФОРТЕ-АЛИУМ» — это
    особенность ввода, а не часть наименования. Капс в списке подсказок
    читается заметно хуже и выглядит как крик, поэтому приводим к обычному
    виду. Названия со смешанным регистром не трогаем: там регистр осмысленный.
    """
    if not text or text != text.upper():
        return text
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-"))
        for word in text.split()
    )


def tag(chunk: str, name: str) -> str:
    m = re.search(rf"<(?:ns2:)?{name}>([^<]*)</(?:ns2:)?{name}>", chunk)
    return m.group(1).strip() if m else ""


def form_of(text: str) -> str:
    upper = text.upper()
    for needle, code in FORMS:
        if needle in upper:
            return code
    return ""


def strength_of(text: str) -> tuple[str, str]:
    """«24 мг/мл» -> ('24', 'MG'). Составные дозировки пропускаются."""
    if not text or "+" in text:
        return "", ""

    m = re.match(r"^\s*([\d.,]+)\s*([А-ЯЁA-Z]+)", text.upper())
    if not m:
        return "", ""

    value, raw_unit = m.group(1).replace(",", "."), m.group(2)
    for needle, code in UNITS:
        if raw_unit.startswith(needle):
            return (value, code) if code else ("", "")
    return "", ""


def main(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")

    rows: dict[str, list[str]] = {}
    records = 0

    for match in KLP.finditer(text):
        chunk = match.group(0)
        barcodes = BARCODE.findall(chunk)
        if not barcodes:
            continue
        records += 1

        name = pretty(tag(chunk, "trade_name"))
        if not name:
            continue

        inn = pretty(tag(chunk, "mnn_norm_name"))
        form = form_of(tag(chunk, "lf_norm_name"))
        strength, unit = strength_of(tag(chunk, "dosage_norm_name"))

        for barcode in set(barcodes):
            # GTIN из кода маркировки — это EAN-13 с ведущим нулём
            # до четырнадцати знаков. Приводим к нему сразу, чтобы
            # приложению не пришлось гадать при поиске.
            gtin = barcode.zfill(14)
            # Один и тот же код может встретиться у нескольких карточек;
            # берём первую — они описывают одну и ту же упаковку.
            rows.setdefault(gtin, [gtin, name, inn, form, strength, unit])

    lines = [";".join(row) for row in rows.values()]
    lines.sort()

    version = re.search(r"(\d{8})", path.name)
    version = version.group(1) if version else "unknown"
    version = f"{version[:4]}-{version[4:6]}-{version[6:]}"

    header = (
        f"# pillbox-gtin version={version} count={len(lines)}\n"
        "#\n"
        "# Штрихкоды упаковок из ЕСКЛП (Минздрав). Собирается скриптом\n"
        "# tools/extract_esklp.py — правьте исходник, а не этот файл.\n"
        "#\n"
        "# Только ЖНВЛП: в ЕСКЛП штрихкод лежит в блоке предельных цен,\n"
        "# а он заполняется лишь для препаратов из перечня. Остальные\n"
        "# лекарства и все БАДы по коду с упаковки не опознаются.\n"
        "#\n"
        "# Код приведён к 14 знакам — так он приходит из кода маркировки.\n"
        "#\n"
        "# Формат: код;название;действующее вещество;форма;дозировка;единица\n"
        "gtin;name;inn;form;strength;unit\n"
    )
    OUT.write_text(header + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with_form = sum(1 for r in rows.values() if r[3])
    with_strength = sum(1 for r in rows.values() if r[4])
    print(f"карточек со штрихкодами: {records}")
    print(f"уникальных кодов: {len(rows)}")
    print(f"  из них с формой выпуска: {with_form}")
    print(f"  из них с дозировкой: {with_strength}")
    print(f"записано в {OUT} ({OUT.stat().st_size / 1048576:.1f} МБ)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))

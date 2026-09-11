#!/usr/bin/env python3
"""Извлекает таблицу «штрихкод — препарат» из выгрузки ЕСКЛП.

ЕСКЛП — единый структурированный справочник-каталог лекарственных препаратов
Минздрава. Из него берутся только фактические сведения с упаковки: штрихкод,
торговое наименование, действующее вещество, форма и дозировка. Описаний,
показаний и прочего защищённого авторским правом содержимого в нём нет
и сюда не попадает.

Важное ограничение: штрихкоды в ЕСКЛП лежат в блоке предельных отпускных цен,
а он заполняется только для ЖНВЛП. Препараты вне этого перечня и тем более
БАДы штрихкодов в выгрузке не имеют. Эти пробелы закрываются вручную —
source/gtin_manual.tsv, коды оттуда подмешиваются в итоговый файл и имеют
приоритет над ЕСКЛП.

Где взять исходники — на портале ЕСКЛП есть два нужных XML:
    active — только действующие записи (~55 МБ архив, ~1 ГБ внутри)
    full   — действующие и исторические (~184 МБ архив, ~3,4 ГБ внутри)

Берутся оба. Исторические записи дают около 2,5 тысяч кодов сверх active:
регистрацию препарата давно прекратили, а упаковка у человека в тумбочке
лежит, и штрихкод на ней рабочий. Сканеру её надо узнавать.

Прямые ссылки (портал — одностраничное приложение, руками их не видно):
    список:  /fs/public/list?createTimestamp=ГГГГ-ММ-ДД&section=esklp&exportFormat=XML
    даты:    /fs/public/file_dates?year=ГГГГ
    файл:    /fs/public/download/<fileId из списка>

Запуск:
    python tools/extract_esklp.py active.xml full.xml
        полная пересборка. Порядок важен: файл, указанный раньше,
        перекрывает следующие, поэтому active идёт первым

    python tools/extract_esklp.py
        только подмешать ручные коды в уже собранный catalog/gtin.csv.
        Нужно, когда добавили пару БАДов и качать гигабайтную выгрузку
        ради этого незачем.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "catalog" / "gtin.csv"
MANUAL = ROOT / "source" / "gtin_manual.tsv"
COLUMNS = 6

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


def read_manual() -> dict[str, list[str]]:
    """Коды, внесённые руками с упаковок на руках.

    Строки проверяются строго и при ошибке роняют сборку: молча пропущенная
    кривая строка означала бы, что человек считает код добавленным, а в
    приложении его нет.
    """
    if not MANUAL.exists():
        return {}

    rows: dict[str, list[str]] = {}
    for number, raw in enumerate(MANUAL.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("gtin\t"):
            continue

        parts = raw.rstrip("\n").split("\t")
        if len(parts) != COLUMNS:
            raise SystemExit(
                f"{MANUAL.name}, строка {number}: колонок {len(parts)}, "
                f"а нужно {COLUMNS} (разделитель — табуляция)"
            )

        gtin, name = parts[0].strip(), parts[1].strip()
        if not gtin.isdigit() or len(gtin) > 14:
            raise SystemExit(f"{MANUAL.name}, строка {number}: код «{gtin}» — не 14 цифр")
        if not name:
            raise SystemExit(f"{MANUAL.name}, строка {number}: пустое название")
        if ";" in raw:
            raise SystemExit(
                f"{MANUAL.name}, строка {number}: точка с запятой ломает CSV — уберите"
            )

        gtin = gtin.zfill(14)
        rows[gtin] = [gtin] + [p.strip() for p in parts[1:]]

    return rows


def read_existing() -> dict[str, list[str]]:
    """Уже собранный каталог — чтобы подмешать ручные коды без выгрузки."""
    if not OUT.exists():
        raise SystemExit(
            f"{OUT} не найден. Для первой сборки нужна выгрузка ЕСКЛП — "
            "запустите скрипт с путём к XML."
        )

    rows: dict[str, list[str]] = {}
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or line.startswith("gtin;"):
            continue
        parts = line.split(";")
        if len(parts) == COLUMNS:
            rows[parts[0]] = parts
    return rows


MANUAL_MARKER = "# manual-gtins: "


def write(rows: dict[str, list[str]], version: str, manual: dict[str, list[str]]) -> None:
    lines = sorted(";".join(row) for row in rows.values())

    # Коды из ручного файла перечисляются в шапке. Без этого пересборка
    # без выгрузки ЕСКЛП умеет только добавлять: удалённую из исходника
    # строку нечем опознать, и ошибочный код застрял бы в каталоге навсегда.
    marker = (MANUAL_MARKER + ",".join(sorted(manual))).rstrip() + "\n"

    header = (
        f"# pillbox-gtin version={version} count={len(lines)}\n"
        "#\n"
        "# Штрихкоды упаковок. Собирается скриптом tools/extract_esklp.py —\n"
        "# правьте исходники, а не этот файл.\n"
        "#\n"
        "# Источники:\n"
        "#   ЕСКЛП (Минздрав), выгрузки active и full — только ЖНВЛП: штрихкод\n"
        "#     там лежит в блоке предельных цен, а он заполняется лишь для\n"
        "#     препаратов из перечня. Из full берутся исторические записи:\n"
        "#     регистрация прекращена, а упаковка у человека осталась;\n"
        "#   source/gtin_manual.tsv — коды с упаковок, внесённые вручную\n"
        f"#     ({len(manual)} шт.): препараты вне перечня и БАДы.\n"
        "#\n"
        "# Код приведён к 14 знакам — так он приходит из кода маркировки.\n"
        "#\n"
        + marker
        + "#\n"
        "# Формат: код;название;действующее вещество;форма;дозировка;единица\n"
        "gtin;name;inn;form;strength;unit\n"
    )
    OUT.write_text(header + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def merge_only() -> int:
    """Режим без выгрузки: пересобрать каталог с текущим ручным файлом."""
    head = OUT.read_text(encoding="utf-8")[:4096]
    rows = read_existing()

    # Сначала снимаем прошлую ручную порцию, потом кладём текущую —
    # иначе удалённая из исходника строка осталась бы в каталоге.
    previous = next(
        (
            line[len(MANUAL_MARKER):].strip()
            for line in head.splitlines()
            if line.startswith(MANUAL_MARKER)
        ),
        "",
    )
    dropped = 0
    for gtin in filter(None, previous.split(",")):
        if rows.pop(gtin, None) is not None:
            dropped += 1

    manual = read_manual()
    rows.update(manual)

    version = re.search(r"version=(\S+)", head)
    write(rows, version.group(1) if version else "unknown", manual)

    print(f"снято прошлых ручных кодов: {dropped}")
    print(f"внесено ручных кодов: {len(manual)}")
    print(f"всего в каталоге: {len(rows)}")
    print(f"записано в {OUT} ({OUT.stat().st_size / 1048576:.1f} МБ)")
    if dropped and not manual:
        print(
            "\nвнимание: ручной файл пуст. Если код был подменой записи ЕСКЛП,\n"
            "она вернётся только полной пересборкой с выгрузкой."
        )
    return 0


CHUNK = 8 << 20


def klp_blocks(path: Path):
    """Выдавать блоки <ns2:klp> по одному, не поднимая файл в память.

    Полная выгрузка с историческими записями — 3,4 ГБ в распакованном виде,
    read_text() на ней съедает всю память. Читаем окном и отдаём только
    целиком закрывшиеся блоки, хвост переносим в следующее окно.
    """
    tail = ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        while True:
            piece = handle.read(CHUNK)
            if not piece:
                break
            tail += piece

            last = 0
            for match in KLP.finditer(tail):
                yield match.group(0)
                last = match.end()

            if last:
                tail = tail[last:]
            elif len(tail) > 4 * CHUNK:
                # Открывающего тега в окне нет вовсе — это не наш кусок
                # файла (шапка, справочники), копить его незачем.
                keep = tail.rfind("<ns2:klp")
                tail = tail[keep:] if keep >= 0 else ""


def collect(path: Path, rows: dict[str, list[str]]) -> tuple[int, int]:
    """Разобрать выгрузку, добавив в rows только ещё не встреченные коды."""
    records = 0
    before = len(rows)

    for chunk in klp_blocks(path):
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
            # Поэтому порядок файлов важен: активные записи идут раньше
            # исторических и перекрывают их.
            rows.setdefault(gtin, [gtin, name, inn, form, strength, unit])

    return records, len(rows) - before


def main(paths: list[Path]) -> int:
    rows: dict[str, list[str]] = {}
    records = 0

    for path in paths:
        seen, added = collect(path, rows)
        records += seen
        print(f"{path.name}: карточек со штрихкодами {seen}, новых кодов {added}")

    from_esklp = len(rows)

    # Ручные коды идут последними и перекрывают ЕСКЛП: они выверены
    # по упаковке в руках, а выгрузка бывает неточной.
    manual = read_manual()
    rows.update(manual)

    version = re.search(r"(\d{8})", paths[0].name)
    version = version.group(1) if version else "unknown"
    version = f"{version[:4]}-{version[4:6]}-{version[6:]}"

    write(rows, version, manual)

    with_form = sum(1 for r in rows.values() if r[3])
    with_strength = sum(1 for r in rows.values() if r[4])
    print(f"карточек со штрихкодами: {records}")
    print(f"кодов из ЕСКЛП: {from_esklp}")
    print(f"добавлено вручную: {len(manual)}")
    print(f"уникальных кодов: {len(rows)}")
    print(f"  из них с формой выпуска: {with_form}")
    print(f"  из них с дозировкой: {with_strength}")
    print(f"записано в {OUT} ({OUT.stat().st_size / 1048576:.1f} МБ)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Без выгрузки — значит, просто подмешать ручные коды.
        sys.exit(merge_only())
    sys.exit(main([Path(a) for a in sys.argv[1:]]))

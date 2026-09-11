#!/usr/bin/env python3
"""Ежемесячное обновление справочника: скачать ЕСКЛП, пересобрать, запушить.

Делает всё, что руками делалось по инструкции из README: узнаёт, есть ли
свежая выгрузка, качает обе (active и full), пересобирает каталог, проверяет
и коммитит с пушем.

Запуск — раз в месяц, чаще незачем: Минздрав обновляет выгрузку неравномерно,
но новых штрихкодов за неделю набегает единицы.

    python tools/update_catalog.py            обычный прогон
    python tools/update_catalog.py --dry-run  всё то же, но без коммита и пуша
    python tools/update_catalog.py --force     пересобрать, даже если версия та же

Что важно знать:

  * Нужно около 6 ГБ свободного места под временные файлы. Они удаляются
    в конце в любом случае, даже если прогон свалился.
  * Если пересборка или проверка не прошли, каталог откатывается из git
    и ничего не пушится. Лучше остаться на прошлой версии, чем выложить
    битый файл: приложение скачивает его себе автоматически.
  * Ручные коды из source/gtin_manual.tsv подмешиваются сами — отдельно
    ничего делать не нужно.
"""

import argparse
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "gtin.csv"
TOOLS = ROOT / "tools"
# Корень и промежуточные НУЦ Минцифры. Промежуточный истекает в 2027-м —
# когда перестанет проверяться, связку надо обновить (как — в open_url).
CA_BUNDLE = TOOLS / "russian_trusted_ca.pem"

HOST = "https://esklp.egisz.rosminzdrav.ru"
DATES = HOST + "/fs/public/file_dates?year={year}"
LIST = HOST + "/fs/public/list?createTimestamp={day}&section=esklp&exportFormat=XML"
DOWNLOAD = HOST + "/fs/public/download/{file_id}"

# Выгрузки, которые нам нужны, в порядке приоритета: указанная раньше
# перекрывает следующие, а действующая запись достовернее исторической.
WANTED = ["fullActive", "full"]

NEED_FREE_GB = 6
TIMEOUT = 120


def ssl_context() -> ssl.SSLContext:
    """Проверять сертификат ЕСКЛП по связке НУЦ Минцифры, а не по системной.

    Сервер ЕСКЛП отдаёт только конечный сертификат, без промежуточного,
    а подписан он «Russian Trusted Sub CA» — корнем Минцифры, которого нет
    ни в OpenSSL, ни в certifi. На Windows это обычно незаметно (корень
    стоит в системном хранилище), на чистом Linux-сервере — падает сразу.

    Связка лежит рядом, в tools/russian_trusted_ca.pem. Так доверие
    ограничено одним этим скриптом: ставить государственный корневой
    сертификат в системное хранилище ради выгрузки справочника — чересчур,
    после этого ему будут доверять вообще все программы на машине.

    Отключать проверку нельзя ни при каком раскладе: собранный файл уезжает
    пользователям в приложение.
    """
    if CA_BUNDLE.exists():
        return ssl.create_default_context(cafile=str(CA_BUNDLE))
    print(f"внимание: нет {CA_BUNDLE.name}, проверяю по системному хранилищу")
    return ssl.create_default_context()


def open_url(url: str):
    try:
        return urllib.request.urlopen(url, timeout=TIMEOUT, context=ssl_context())
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise SystemExit(
                f"сертификат ЕСКЛП не прошёл проверку: {reason}\n"
                f"Возможно, Минцифры сменили промежуточный сертификат — тогда\n"
                f"обновите {CA_BUNDLE.name}: корень берётся с\n"
                f"  https://gu-st.ru/content/Other/doc/russiantrustedca.pem\n"
                f"промежуточный — по ссылке из самого сертификата (AIA):\n"
                f"  http://nuc-cdp.digital.gov.ru/cdp/subca_ssl_rsa2024.crt"
            )
        raise SystemExit(f"не достучался до портала ЕСКЛП: {reason}")


def fetch_json(url: str):
    with open_url(url) as response:
        return json.load(response)


def latest_day() -> str:
    """Дата свежайшей выгрузки на портале."""
    year = date.today().year
    for candidate in (year, year - 1):
        data = fetch_json(DATES.format(year=candidate))
        last = (data.get("last") or {}).get("archive_date")
        if last:
            return last
        if data.get("history"):
            return max(data["history"])
    raise SystemExit("портал ЕСКЛП не отдал ни одной даты выгрузки")


def current_version() -> str:
    """Версия уже собранного каталога — из первой строки файла."""
    if not CATALOG.exists():
        return ""
    head = CATALOG.read_text(encoding="utf-8").split("\n", 1)[0]
    for part in head.split():
        if part.startswith("version="):
            return part[len("version="):]
    return ""


def download(file_id: str, name: str, into: Path) -> Path:
    target = into / f"{name}.zip"
    print(f"  качаю {name}...", flush=True)
    with open_url(DOWNLOAD.format(file_id=file_id)) as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=4 << 20)

    # Портал на несуществующий идентификатор отвечает JSON'ом с ошибкой,
    # а не кодом 404 — молча получить 153 байта вместо архива легко.
    if not zipfile.is_zipfile(target):
        raise SystemExit(
            f"{name}: пришёл не архив ({target.stat().st_size} байт). "
            "Похоже, портал вернул ошибку вместо файла."
        )

    print(f"    {target.stat().st_size / 1048576:.0f} МБ, распаковываю", flush=True)
    with zipfile.ZipFile(target) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".xml")]
        if not names:
            raise SystemExit(f"{name}: в архиве нет XML")
        archive.extract(names[0], into)
    target.unlink()
    return into / names[0]


def run(*command: str) -> None:
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"не выполнилось: {' '.join(command)}")


def git(*args: str) -> str:
    result = subprocess.run(
        ("git",) + args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="не коммитить и не пушить")
    parser.add_argument("--force", action="store_true", help="пересобрать даже без новой выгрузки")
    parser.add_argument("--no-push", action="store_true", help="закоммитить, но не пушить")
    options = parser.parse_args()

    day = latest_day()
    have = current_version()
    print(f"свежая выгрузка на портале: {day}")
    print(f"в каталоге сейчас:          {have or '—'}")

    if day == have and not options.force:
        print("\nуже актуально, качать нечего.")
        return 0

    free_gb = shutil.disk_usage(tempfile.gettempdir()).free / 1024**3
    if free_gb < NEED_FREE_GB:
        raise SystemExit(
            f"на диске под временные файлы свободно {free_gb:.1f} ГБ, "
            f"нужно около {NEED_FREE_GB}"
        )

    files = fetch_json(LIST.format(day=day))["results"]
    by_type = {f["fileKeyValueAttributes"].get("exportType"): f for f in files}
    missing = [w for w in WANTED if w not in by_type]
    if missing:
        raise SystemExit(f"на портале за {day} нет выгрузок: {', '.join(missing)}")

    workdir = Path(tempfile.mkdtemp(prefix="esklp-"))
    try:
        xml_paths = [
            download(by_type[kind]["fileId"], kind, workdir) for kind in WANTED
        ]

        print("\nпересобираю каталог...", flush=True)
        run(sys.executable, str(TOOLS / "extract_esklp.py"), *map(str, xml_paths))

        print("\nпроверяю...", flush=True)
        run(sys.executable, str(TOOLS / "validate_catalog.py"))
    except BaseException:
        # Битый каталог хуже устаревшего: приложение скачивает его себе
        # само, и полусобранный файл разъедется по пользователям.
        # Откатываем и при --dry-run: файл генерируемый, руками его
        # всё равно никто не правит.
        #
        # Откат сам по себе упасть не должен, но если упадёт — не заслоняем
        # им настоящую причину сбоя.
        try:
            git("checkout", "--", CATALOG.relative_to(ROOT).as_posix())
            print("\nкаталог откачен к прошлой версии.")
        except SystemExit as rollback_error:
            print(f"\nоткатить каталог не вышло: {rollback_error}")
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if options.dry_run:
        print("\n--dry-run: коммит и пуш пропущены.")
        return 0

    if not git("status", "--porcelain", "catalog"):
        print("\nкаталог не изменился — коммитить нечего.")
        return 0

    git("add", "catalog")
    git("commit", "-m", f"Обновление справочника ЕСКЛП до {day}")
    print(f"\nзакоммичено: обновление до {day}")

    if options.no_push:
        print("--no-push: осталось запушить руками.")
        return 0

    git("push", "origin", git("rev-parse", "--abbrev-ref", "HEAD"))
    print("запушено. Приложение подхватит обновление в течение суток.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

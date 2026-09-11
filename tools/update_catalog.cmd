@echo off
rem Обёртка для Планировщика заданий Windows.
rem
rem Планировщику неудобно запускать python напрямую: нужен правильный
rem рабочий каталог (иначе git не найдёт репозиторий), кодировка вывода
rem и куда-то девать сообщения. Здесь всё это и делается.
rem
rem Лог лежит рядом с репозиторием, а не внутри — чтобы не попадать в git:
rem   %LOCALAPPDATA%\pillbox-catalog-update.log
rem
rem Зарегистрировать задачу (раз в месяц, 1-го числа в 10:00):
rem   schtasks /create /tn "PillboxCatalogUpdate" /sc MONTHLY /d 1 /st 10:00 ^
rem     /tr "\"E:\Devolop\Farm\tools\update_catalog.cmd\""
rem
rem Проверить прямо сейчас, не дожидаясь расписания:
rem   schtasks /run /tn "PillboxCatalogUpdate"

setlocal
set "PYTHONIOENCODING=utf-8"
set "LOG=%LOCALAPPDATA%\pillbox-catalog-update.log"

cd /d "%~dp0.."

echo. >> "%LOG%"
echo ==== %DATE% %TIME% ==== >> "%LOG%"

python tools\update_catalog.py %* >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo ОШИБКА: код возврата %CODE% >> "%LOG%"
)

exit /b %CODE%

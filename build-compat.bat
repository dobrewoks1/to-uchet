@echo off
rem Сборка «Учёт ТО» для старых и слабых ПК: один 32-битный exe на Python 3.8 (onefile, без консоли).
rem Результат: dist\TO-Uchet.exe — работает на Windows 7 SP1, 8.1, 10, 11, 32 и 64 бит. ЭТА сборка отдаётся заказчику.
rem Обычная сборка build.bat — 64 бит, Python 3.14 из .venv, только Windows 10/11 (для разработки).
rem Обе пишут dist\TO-Uchet.exe — последняя запущенная перезаписывает файл.
rem
rem Python 3.8 x86 — переменная окружения PY38 (полный путь к python.exe), по умолчанию — путь ниже.
rem Нужные пакеты:  "%PY38%" -m pip install "openpyxl==3.1.5" "pyinstaller==6.22.3"
rem
rem Почему Python 3.8: последняя версия Python для Windows 7 (3.9+ на Windows 7 не запускаются).
rem Почему 32 бит: 32-битный exe работает и на 32-, и на 64-битной Windows.
rem
rem Почему PyInstaller 6.22.3, а не 5.13.2 (проверено 10.10.2026):
rem  - официально Windows 7 не обещают ни 5.x, ни 6.x: в документации обеих веток одна фраза
rem    «PyInstaller runs in Windows 8 and newer» — она про ПК, на котором идёт сборка;
rem  - загрузчик (bootloader) 6.22.3 по-прежнему компилируется под Windows 7: в bootloader\wscript заданы
rem    NTDDI_VERSION=0x06010000 и _WIN32_WINNT=0x0601 (так же с версии 4.7, т.е. и в 5.x);
rem  - таблица импорта PyInstaller\bootloader\Windows-32bit-intel\runw.exe 6.22.3: только USER32, KERNEL32,
rem    ADVAPI32, GDI32, COMCTL32 и только функции, которые есть в Windows 7; в заголовке PE — ОС 6.0;
rem  - единственный вызов с флагом LOAD_LIBRARY_SEARCH_DEFAULT_DIRS (на Windows 7 без KB2533623 не работает) —
rem    необязательная предзагрузка VC runtime в onefile, ошибка там не фатальна.
rem  Значит, отката на 5.13.2 не требуется; 6.22.3 новее и с исправлениями onefile.
rem
rem Что нужно самой Windows 7 (так требует Python 3.8, от PyInstaller не зависит):
rem  - SP1 и обновление KB2533623 (на обновлённых ПК обычно уже есть);
rem  - Universal C Runtime (обновление KB2999226; ставится и вместе с Visual C++ Redistributable 2015-2019 x86).
rem    Без него при запуске — «api-ms-win-crt-runtime-l1-1-0.dll отсутствует».
rem vcruntime140.dll PyInstaller кладёт в exe сам (из папки Python 3.8) — скрипт это проверяет в конце.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not defined PY38 set "PY38=%~dp0.py38\python.exe"

if not exist "%PY38%" (
  echo Не найден Python 3.8: "%PY38%"
  echo Укажите путь к python.exe 32-битного Python 3.8 в переменной PY38, например:
  echo   set PY38=C:\py38\python.exe
  exit /b 1
)
"%PY38%" -c "import sys, struct; sys.exit(0 if sys.version_info[:2] == (3, 8) and struct.calcsize('P') == 4 else 1)"
if errorlevel 1 (
  echo "%PY38%" — это не 32-битный Python 3.8. Нужен именно он: только такой exe запускается на Windows 7 и 32-битных системах.
  exit /b 1
)
"%PY38%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo В Python 3.8 нет PyInstaller. Установите: "%PY38%" -m pip install "openpyxl==3.1.5" "pyinstaller==6.22.3"
  exit /b 1
)
"%PY38%" -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  echo В Python 3.8 нет openpyxl. Установите: "%PY38%" -m pip install "openpyxl==3.1.5"
  exit /b 1
)
if not exist "%~dp0app\static\index.html" (
  echo Не найден app\static\index.html — фронтенд не на месте, собирать нечего.
  exit /b 1
)

rem Рабочая папка и spec — в build\compat, чтобы не смешиваться с обычной сборкой (build.bat).
rem --noupx: если на ПК окажется UPX, сжатые им exe/dll чаще ложно ловят антивирусы на старых ПК.
"%PY38%" -m PyInstaller --noconfirm --clean --onefile --noconsole --noupx --name TO-Uchet ^
  --paths "%~dp0." ^
  --add-data "%~dp0app\static;app/static" ^
  --hidden-import app.server --hidden-import app.services --hidden-import app.db ^
  --hidden-import app.logic --hidden-import app.xlsx_io ^
  --distpath "%~dp0dist" --workpath "%~dp0build\compat" --specpath "%~dp0build\compat" ^
  "%~dp0app\main.py"
if errorlevel 1 (
  echo.
  echo СБОРКА НЕ УДАЛАСЬ — смотрите сообщения выше.
  exit /b 1
)

rem Проверка: внутри exe есть VC runtime, Python и SQLite.
"%PY38%" -c "import sys; from PyInstaller.archive.readers import CArchiveReader; names = {n.lower() for n in CArchiveReader(sys.argv[1]).toc}; miss = [n for n in ('vcruntime140.dll', 'python38.dll', 'sqlite3.dll') if n not in names]; print('ВНИМАНИЕ: в exe нет ' + ', '.join(miss) if miss else 'Проверено: vcruntime140.dll, python38.dll и sqlite3.dll внутри exe.'); sys.exit(1 if miss else 0)" "%~dp0dist\TO-Uchet.exe"
if errorlevel 1 exit /b 1

echo.
echo Готово: "%~dp0dist\TO-Uchet.exe" (32 бит, Python 3.8, Windows 7 SP1 и новее)
echo Данные программа хранит в папке data рядом с exe (база to.db, копии backups, журнал app.log).
endlocal

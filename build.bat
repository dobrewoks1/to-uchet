@echo off
rem Сборка «Учёт ТО» в один exe (PyInstaller onefile, без консоли). Результат: dist\TO-Uchet.exe
rem Запускать из любой папки: скрипт сам переходит в папку проекта.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\pyinstaller.exe" (
  echo Не найден "%~dp0.venv\Scripts\pyinstaller.exe" — создайте .venv и установите pyinstaller и openpyxl.
  exit /b 1
)
if not exist "%~dp0app\static\index.html" (
  echo Не найден app\static\index.html — фронтенд не на месте, собирать нечего.
  exit /b 1
)

"%~dp0.venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --noconsole --name TO-Uchet ^
  --paths "%~dp0." ^
  --add-data "%~dp0app\static;app/static" ^
  --hidden-import app.server --hidden-import app.services --hidden-import app.db ^
  --hidden-import app.logic --hidden-import app.xlsx_io ^
  --distpath "%~dp0dist" --workpath "%~dp0build" --specpath "%~dp0build" ^
  "%~dp0app\main.py"
if errorlevel 1 (
  echo.
  echo СБОРКА НЕ УДАЛАСЬ — смотрите сообщения выше.
  exit /b 1
)

echo.
echo Готово: "%~dp0dist\TO-Uchet.exe"
echo Данные программа хранит в папке data рядом с exe (база to.db, копии backups, журнал app.log).
endlocal

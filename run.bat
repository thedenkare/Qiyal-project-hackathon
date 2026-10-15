@echo off
REM MarginGuard: запуск одной командой (Windows). Нужен Python 3.11+ (python.org, галочка "Add to PATH").
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Создаю виртуальное окружение...
  python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt || goto :err
if not exist .env copy .env.example .env >nul
if "%PORT%"=="" set PORT=8000
echo Откройте http://localhost:%PORT%
python -m uvicorn app.web.main:app --host 0.0.0.0 --port %PORT%
goto :eof
:err
echo Ошибка установки. Проверьте, что установлен Python 3.11+ и есть интернет для первой установки пакетов.
pause

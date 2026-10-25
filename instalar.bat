@echo off
REM Instala o que o painel precisa (rodar UMA vez neste PC)
cd /d "%~dp0"
echo Instalando bibliotecas do Python...
python -m pip install --upgrade flask openpyxl playwright
echo.
echo Instalando o navegador de reserva do leitor (caso o Chrome falhe)...
python -m playwright install chromium
echo.
echo Pronto. Agora rode o login_messenger.bat para entrar no Messenger.
pause

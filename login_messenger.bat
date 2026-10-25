@echo off
REM Faz o login no Messenger UMA vez. A senha NAO fica salva:
REM so a sessao, na pasta "perfil_messenger".
REM Antes de rodar, FECHE o painel (iniciar_painel.bat), se estiver aberto.
REM Passo a passo: login no Onvio -> abrir o Messenger -> FECHAR o Chrome.
cd /d "%~dp0"
REM fecha o leitor, se estiver rodando (ele usa o mesmo perfil do Chrome)
taskkill /FI "WINDOWTITLE eq Leitor Messenger*" /T /F >nul 2>nul
timeout /t 2 /nobreak >nul
python leitor_messenger.py --login
if %errorlevel%==0 (
    echo.
    echo Religando o leitor do Messenger ^(janela minimizada^)...
    start "Leitor Messenger" /min cmd /k python leitor_messenger.py
    echo Pronto! Em ate 30 segundos o card da TV fica "AO VIVO".
    echo Se o painel ^(iniciar_painel.bat^) estiver fechado, abra-o tambem.
)
pause

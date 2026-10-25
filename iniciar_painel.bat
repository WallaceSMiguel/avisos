@echo off
cd /d "%~dp0"
echo Iniciando o leitor do Messenger (janela minimizada)...
start "Leitor Messenger" /min cmd /k python leitor_messenger.py
echo Iniciando o Painel de Avisos...
echo Na TV, abra:  http://IP-DESTE-PC:5000
echo Diagnostico:   http://IP-DESTE-PC:5000/diagnostico
echo (para ver o IP deste PC, rode "ipconfig" e veja o Endereco IPv4)
echo.
python app.py
pause

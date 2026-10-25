@echo off
REM ============================================================
REM  CONVERTER VIDEOS PARA TOCAR NA TV
REM  - 1920x1080 (TVs nao tocam 4K pelo navegador)
REM  - H.264 Main 4.0 + AAC (formato aceito por todas as TVs)
REM  - faststart (indice no inicio: comeca a tocar na hora)
REM  Os arquivos originais ficam na subpasta "originais"
REM  Precisa do FFmpeg instalado:  winget install Gyan.FFmpeg
REM ============================================================
setlocal enabledelayedexpansion
REM Converte os videos da pasta onde este .bat estiver.
REM Pode copiar este arquivo para a pasta compartilhada de midia.
cd /d "%~dp0"
if exist "%~dp0static\avisos" cd /d "%~dp0static\avisos"

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo.
    echo  FFmpeg nao encontrado.
    echo  Abra o Prompt de Comando e rode:  winget install Gyan.FFmpeg
    echo  Depois feche e abra de novo este arquivo.
    echo.
    pause
    exit /b 1
)

if not exist originais mkdir originais

for %%F in (*.mp4 *.m4v *.mov *.webm) do (
    if exist "originais\%%F" (
        echo [pulando] %%F ja foi convertido antes
    ) else (
        echo [convertendo] %%F ...
        ffmpeg -y -hide_banner -loglevel error -i "%%F" -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2,fps=30" -c:v libx264 -preset medium -profile:v main -level 4.0 -pix_fmt yuv420p -crf 23 -maxrate 5M -bufsize 10M -c:a aac -b:a 128k -ac 2 -ar 44100 -movflags +faststart -f mp4 "%%~nF.tvtmp"
        if !errorlevel! equ 0 (
            move /y "%%F" "originais\%%F" >nul
            move /y "%%~nF.tvtmp" "%%~nF.mp4" >nul
            echo [ok] %%~nF.mp4
        ) else (
            echo [ERRO] nao foi possivel converter %%F
            if exist "%%~nF.tvtmp" del "%%~nF.tvtmp"
        )
    )
)

echo.
echo Concluido. Se algum video era .mov ou .webm, ele virou .mp4:
echo ajuste o nome na coluna Imagem da planilha dados\avisos.xlsx
echo.
pause

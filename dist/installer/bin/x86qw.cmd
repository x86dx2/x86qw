@echo off
set "X86QW_ROOT=%~dp0"
if "%~1"=="" goto help
if /I "%~1"=="help" goto help
if /I "%~1"=="-h" goto help
if /I "%~1"=="--help" goto help
if /I "%~1"=="play" goto play
if /I "%~1"=="update" goto maintenance
if /I "%~1"=="upgrade" goto maintenance
if /I "%~1"=="hub" goto maintenance
if /I "%~1"=="verify" goto maintenance
if /I "%~1"=="cleanup" goto maintenance
if /I "%~1"=="uninstall" goto maintenance
echo x86qw: comando desconhecido: %~1 1>&2
goto help_error

:play
py -3 "%X86QW_ROOT%.install\cli\dist\installer\bin\gameplay.py" "%X86QW_ROOT%" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:maintenance
set "X86QW_ACTION=%~1"
py -3 "%X86QW_ROOT%.install\cli\dist\installer\bin\manager.py" --online-only --installed-cli %1 "%X86QW_ROOT%" %2 %3 %4 %5 %6 %7 %8 %9
set "X86QW_EXIT=%ERRORLEVEL%"
if /I "%X86QW_ACTION%"=="uninstall" if "%X86QW_EXIT%"=="0" del "%~f0"
exit /b %X86QW_EXIT%

:help
echo x86QW - QuakeWorld moderno
echo.
echo Uso: x86qw.cmd ^<comando^> [opcoes]
echo.
echo Gameplay:
echo   play                 escolhe e inicia um mod local
echo   hub                  lista servidores publicos
echo.
echo Manutencao:
echo   update [--yes]       atualiza o conteudo ja instalado
echo   upgrade [--yes]      incorpora novidades do perfil
echo   verify               verifica a instalacao
echo   cleanup              limpa o cache x86QW
echo   uninstall            preserva PAKs e dados pessoais
echo   uninstall --purge    remove completamente o x86QW
echo   help                 mostra esta ajuda
echo.
echo A instalacao e exclusiva do install.ps1.
exit /b 0

:help_error
call :help
exit /b 2

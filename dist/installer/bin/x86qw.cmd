@echo off
setlocal DisableDelayedExpansion
for %%I in ("%~dp0.") do set "X86QW_ROOT=%%~fI"
set "X86QW_APP=%X86QW_ROOT%\.x86qw\cli\x86qw.pyz"
set "X86QW_PYTHON=@X86QW_PYTHON@"
set "X86QW_PYTHON_ARGS="
call :resolve_python
if errorlevel 1 exit /b %ERRORLEVEL%
if "%~1"=="" goto menu
if /I "%~1"=="help" goto help
if /I "%~1"=="-h" goto help
if /I "%~1"=="--help" goto help
if /I "%~1"=="version" goto version
if /I "%~1"=="-V" goto version
if /I "%~1"=="--version" goto version
if /I "%~1"=="play" goto play
if /I "%~1"=="host" goto service
if /I "%~1"=="proxy" goto service
if /I "%~1"=="qtv" goto service
if /I "%~1"=="status" goto service
if /I "%~1"=="doctor" goto service
if /I "%~1"=="update" goto maintenance
if /I "%~1"=="upgrade" goto maintenance
if /I "%~1"=="hub" goto maintenance
if /I "%~1"=="verify" goto maintenance
if /I "%~1"=="changes" goto maintenance
if /I "%~1"=="profile" goto maintenance
if /I "%~1"=="library" goto maintenance
if /I "%~1"=="migrate" goto maintenance
if /I "%~1"=="repair" goto maintenance
if /I "%~1"=="cleanup" goto maintenance
if /I "%~1"=="uninstall" goto maintenance
echo x86qw: comando desconhecido: %~1 1>&2
goto help_error

:play
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% "%X86QW_APP%" %* --target "%X86QW_ROOT%"
exit /b %ERRORLEVEL%

:menu
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% "%X86QW_APP%" menu "%X86QW_ROOT%"
exit /b %ERRORLEVEL%

:service
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% "%X86QW_APP%" %* --target "%X86QW_ROOT%"
exit /b %ERRORLEVEL%

:maintenance
set "X86QW_ACTION=%~1"
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% "%X86QW_APP%" --online-only --installed-cli %* "%X86QW_ROOT%"
set "X86QW_EXIT=%ERRORLEVEL%"
if /I "%X86QW_ACTION%"=="uninstall" if "%X86QW_EXIT%"=="0" if /I not "%~2"=="--help" if /I not "%~2"=="-h" del "%~f0"
exit /b %X86QW_EXIT%

:help
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% "%X86QW_APP%" --version
echo QuakeWorld moderno
echo.
echo Uso: x86qw.cmd ^<comando^> [opcoes]
echo.
echo Gameplay:
echo   play                 escolhe e inicia um mod ou modo KTX local
echo   play ktx --mode MODO inicia KTX diretamente no modo informado
echo   hub                  lista servidores publicos
echo.
echo Servicos:
echo   host                 escolhe e hospeda somente o servidor de um jogo
echo   host JOGO            hospeda KTX, Final Arena, Pro-X, TF ou TD2
echo   proxy                inicia o proxy QWFWD
echo   qtv                  inicia o relay web/MVD QTV
echo   status               mostra servicos ativos, PIDs, endpoints e parametros
echo.
echo Manutencao:
echo   update [--yes]       atualiza o conteudo ja instalado
echo   upgrade [--yes]      incorpora novidades do perfil
echo   verify               verifica a instalacao
echo   doctor [--bundle]    diagnostica a instalacao sem alterar arquivos
echo   profile [--backup|--restore]  configuracoes pessoais, fora de cache e demos
echo   library [--add|--remove]  favoritos e recentes locais, com origem e freshness
echo   changes [--sync-gitignore] compara mudancas locais com a instalacao registrada
echo   migrate [--dry-run]   migra metadados para o contrato 1.0
echo   repair [--dry-run]   diagnostica e repara conteudo gerenciado
echo   cleanup              limpa o cache x86QW
echo   uninstall            preserva PAKs e dados pessoais
echo   uninstall --purge    remove completamente o x86QW
echo   version              mostra a versao da CLI instalada
echo   help                 mostra esta ajuda
echo.
echo A instalacao e exclusiva do install.ps1.
exit /b 0

:version
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% "%X86QW_APP%" --version
exit /b %ERRORLEVEL%

:help_error
call :help
exit /b 2

:resolve_python
if exist "%X86QW_PYTHON%" (
  "%X86QW_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 exit /b 0
)

set "X86QW_PYTHON=py"
set "X86QW_PYTHON_ARGS=-3"
"%X86QW_PYTHON%" %X86QW_PYTHON_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0

set "X86QW_PYTHON=python3"
set "X86QW_PYTHON_ARGS="
"%X86QW_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0

set "X86QW_PYTHON=python"
"%X86QW_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0

echo x86QW: Python 3.10 ou mais recente nao foi encontrado. 1>&2
echo Instale com: winget install --id Python.Python.3.13 -e 1>&2
echo Depois abra um novo terminal e execute o comando novamente. 1>&2
exit /b 9009

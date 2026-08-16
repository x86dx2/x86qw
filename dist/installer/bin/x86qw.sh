#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
app="$root/.x86qw/cli/x86qw.pyz"
persisted_python=@X86QW_PYTHON@
unrendered_python='@X86QW_''PYTHON@'

python_is_supported() {
  [ -x "$1" ] || return 1
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

resolve_python() {
  if [ "$persisted_python" != "$unrendered_python" ] && python_is_supported "$persisted_python"; then
    printf '%s\n' "$persisted_python"
    return 0
  fi

  for candidate in python3 python; do
    resolved=$(command -v "$candidate" 2>/dev/null) || continue
    if python_is_supported "$resolved"; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

python_runtime=$(resolve_python) || {
  printf 'x86qw: Python 3.10 ou mais recente não foi encontrado. Instale-o e execute novamente.\n' >&2
  exit 1
}

show_help() {
  if [ -f "$app" ]; then
    "$python_runtime" "$app" --version
  else
    printf 'x86QW\n'
  fi
  cat <<'EOF'
QuakeWorld moderno

Uso: ./x86qw.sh <comando> [opções]

Gameplay:
  play                 escolhe e inicia um mod ou modo KTX local
  play ktx --mode MODO inicia KTX diretamente em duel, 2on2, 4on4 e outros
  hub                  lista servidores públicos para jogar ou observar

Serviços:
  host                 escolhe e hospeda somente o servidor de um jogo
  host JOGO            hospeda KTX, Final Arena, Pro-X, Team Fortress ou TD2
  proxy                inicia o proxy QWFWD
  qtv                  inicia o relay web/MVD QTV
  status               mostra serviços ativos, PIDs, endpoints e parâmetros

Manutenção:
  update [--yes]       atualiza somente clientes e componentes já instalados
  upgrade [--yes]      incorpora novidades do perfil da instalação
  verify               verifica a integridade da instalação
  doctor [--bundle]    diagnostica a instalação sem alterar arquivos
  profile [--backup|--restore]  configurações pessoais, fora de cache e demos
  library [--add|--remove]  favoritos e recentes locais, com origem e freshness
  changes [--sync-gitignore] compara mudanças locais com a instalação registrada
  migrate [--dry-run]   migra metadados para o contrato 1.0
  repair [--dry-run]   diagnostica e repara conteúdo gerenciado
  cleanup              limpa o cache gerenciado pelo x86QW
  uninstall            remove o x86QW e preserva PAKs e dados pessoais
  uninstall --purge    remove completamente instalação, dados e cache
  version              mostra a versão da CLI instalada
  help                 mostra esta ajuda

Exemplos:
  ./x86qw.sh play
  ./x86qw.sh play ktx --mode duel
  ./x86qw.sh play ktx --mode duel --bots 1
  ./x86qw.sh host ktx --mode 4on4 --map dm3 --bind 0.0.0.0
  ./x86qw.sh host team-fortress --map 2fort5r

A instalação inicial e a adição de conteúdo são exclusivas do install.sh.
EOF
}

case "${1:-}" in
  '') exec "$python_runtime" "$app" menu "$root" ;;
  help|-h|--help) show_help; exit 0 ;;
  version|-V|--version) exec "$python_runtime" "$app" --version ;;
  play|host|proxy|qtv|status|doctor) action=$1; shift; exec "$python_runtime" "$app" "$action" "$@" --target "$root" ;;
  update|upgrade|hub|verify|changes|profile|library|migrate|repair|cleanup|uninstall) action=$1; shift ;;
  *) printf 'x86qw: comando desconhecido: %s\n\n' "$1" >&2; show_help >&2; exit 2 ;;
esac

exec "$python_runtime" "$app" --online-only --installed-cli "$action" "$root" "$@"

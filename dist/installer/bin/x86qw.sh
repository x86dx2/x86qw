#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app="$root/.install/cli/x86qw.pyz"

show_help() {
  if [ -f "$app" ]; then
    python3 "$app" --version
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

Manutenção:
  update [--yes]       atualiza somente clientes e componentes já instalados
  upgrade [--yes]      incorpora novidades do perfil da instalação
  verify               verifica a integridade da instalação
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
  '') show_help; exit 0 ;;
  help|-h|--help) show_help; exit 0 ;;
  version|-V|--version) exec python3 "$app" --version ;;
  play|host|proxy|qtv) action=$1; shift; exec python3 "$app" "$action" "$@" --target "$root" ;;
  update|upgrade|hub|verify|cleanup|uninstall) action=$1; shift ;;
  *) printf 'x86qw: comando desconhecido: %s\n\n' "$1" >&2; show_help >&2; exit 2 ;;
esac

exec python3 "$app" --online-only --installed-cli "$action" "$root" "$@"

#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app="$root/.install/cli/x86qw.pyz"

show_help() {
  cat <<'EOF'
x86QW · QuakeWorld moderno

Uso: ./x86qw.sh <comando> [opções]

Gameplay:
  play                 escolhe e inicia um mod local
  hub                  lista servidores públicos para jogar ou observar

Manutenção:
  update [--yes]       atualiza somente clientes e componentes já instalados
  upgrade [--yes]      incorpora novidades do perfil da instalação
  verify               verifica a integridade da instalação
  cleanup              limpa o cache gerenciado pelo x86QW
  uninstall            remove o x86QW e preserva PAKs e dados pessoais
  uninstall --purge    remove completamente instalação, dados e cache
  help                 mostra esta ajuda

Exemplo: ./x86qw.sh play

A instalação inicial e a adição de conteúdo são exclusivas do install.sh.
EOF
}

case "${1:-}" in
  '') show_help; exit 0 ;;
  help|-h|--help) show_help; exit 0 ;;
  play) shift; exec python3 "$app" play "$root" "$@" ;;
  update|upgrade|hub|verify|cleanup|uninstall) action=$1; shift ;;
  *) printf 'x86qw: comando desconhecido: %s\n\n' "$1" >&2; show_help >&2; exit 2 ;;
esac

exec python3 "$app" --online-only --installed-cli "$action" "$root" "$@"

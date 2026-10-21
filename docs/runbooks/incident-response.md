# Resposta a incidente de release

1. Pare o que está publicando: cancele o workflow em andamento e não faça
   merge de PRs que mexam em `site/public`. Não sobrescreva tags nem pacotes.
2. Preserve logs, o catálogo, os metadados TUF e os assets da release, sem
   segredos.
3. Confira a cadeia pública com `go -C go run ./cmd/x86qw-release monitor
   --checkout` e o catálogo autenticado com `x86qw catalog`.
4. Se uma chave estiver envolvida, siga o
   [runbook de comprometimento](trust-compromise.md).
5. Publique a correção somente com um novo commit, uma nova versão da CLI ou do
   pacote e uma nova geração TUF; o artefato problemático permanece imutável
   para auditoria.
6. Registre impacto, plataformas afetadas e o que falhou numa issue.

Não altere PAKs nem reescreva a proveniência dos componentes upstream durante
um incidente.

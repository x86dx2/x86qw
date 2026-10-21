## Resultado

<!-- O que muda para jogador, operador ou mantenedor? -->

## Contexto

<!-- Qual problema ou issue motivou a mudança? -->

## Validação

- [ ] `go -C go test ./...` e `go -C go run ./cmd/x86qw-content verify`
- [ ] Comandos afetados conferidos com `x86qw COMANDO --help`
- [ ] `cd site && npm test && npm run deploy:dry-run`, quando o site muda
- [ ] Sistema em que a mudança foi usada:
- [ ] README e `docs/` continuam descrevendo o que o código faz

## Distribuição e proveniência

- [ ] Nenhum novo byte distribuído
- [ ] Ou: origem, versão, licença, consumidor, tamanho e SHA-256 foram registrados
- [ ] Upstream e customização x86QW permanecem separados
- [ ] Artefatos publicados existentes não foram alterados

## Risco e reversão

<!-- O que pode regredir e como desfazer com segurança? Remova segredos de logs. -->

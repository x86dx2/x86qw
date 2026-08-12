# Nota histórica da estabilização até 1.0

Este arquivo preserva o contexto do checkpoint inicial de estabilização. Ele
não é a fonte atual de status, release ou trust. Para o estado executável,
consulte [PROJECT-STATUS.md](../PROJECT-STATUS.md) e, para a sequência de
execução, [stabilization-1.0-plan.md](stabilization-1.0-plan.md).

## Baseline preservada

O trabalho começou na linha pública `0.7.3`. A versão pública corrente é
`0.7.13`, preparada no commit
`04a55aed8711ec5466dc70f0e33a591d92e07ccb` e seguida por
`origin/main@d4a92c0fe29786fdc6ec5c7d978813cb634be62c`. Nenhum bundle público
anterior é reescrito por esta linha local.

As seis frentes históricas abaixo foram incorporadas antes deste ciclo:

| Frente histórica | Release observada | Limite da evidência |
|---|---|---|
| bootstrap Python | 0.7.1 | contratos portáveis e launchers daquele commit |
| downloader limitado | 0.7.3 | limites, deadline e retry daquele commit |
| ZIP/PK3/PYZ | 0.7.3 | fronteira de archives daquele commit |
| DACL privada Windows | 0.7.3 | código e testes históricos; não execução Windows atual |
| stable macOS upstream | 0.7.3 | preservação do bundle; não notarização/execução M3 |
| fronteiras de runtime | 0.7.3 | integração histórica; não aprovação de 1.0 |

Uma matriz portátil ou uma nota de release não prova smoke nativo, publicação,
custódia de produção ou convergência de mirrors.

## Estado corretivo atual

O checkpoint local contém correções incrementais para:

- SemVer, schemas, launchers e comandos `changes`/`migrate`;
- migração baseada em fixtures reais, incluindo a release pública 0.7.13;
- TUF padrão com root Ed25519 incorporada, cache privado e fail-closed;
- candidate construído uma vez, publisher sem rebuild silencioso e verificação
  explícita de bytes;
- workflows separados para validação portátil, candidato, evidência M3,
  aprovação, mirrors e metadata-last;
- harness M3 que exige host real, plano fechado, candidato exato e artefato para
  cada assertion;
- regressões de processo macOS e projeção de dependências do runtime.

Essas mudanças são locais e não foram publicadas neste ciclo. A fixture de
trust RSA em `maintenance/inventory/trust/` permanece somente como material
histórico dos testes legados; o runtime instalado usa a root TUF padrão em
`maintenance/trust/root.json` e não recebe chaves privadas do repositório.

## Gates ainda abertos

- executar o plano fechado do harness no Mac M3 contra o candidato exato;
- configurar custódia, threshold humano, signer agendado, monitor e recuperação
  de expiração para metadata de produção;
- verificar publicação idempotente nos mirrors e publicar metadata TUF somente
  depois dos assets;
- repetir a suíte crítica sobre os bytes finais e alinhar a documentação após a
  publicação autorizada.

Linux, Windows, macOS Intel e nightly continuam `preview`/`not-run` neste
escopo. A presença de artefato não é alegação de execução nativa.

## Regra de leitura

Datas, contagens de testes, runs de CI e mensagens de erro que apareciam na
versão anterior desta nota eram observações daquele checkpoint e não devem ser
usadas como evidência corrente. A evidência atual precisa apontar para comando,
hash, endpoint, fixture ou resultado reproduzido no relatório da auditoria.

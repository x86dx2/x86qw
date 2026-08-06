# Runbook — Operações de trust metadata

- **Estado:** proposta operacional; nenhuma chave ou metadata existe nesta PR
- **Autoridade técnica:** [ADR 0006](../adr/0006-tuf-trust-metadata.md)
- **Issue:** [#48](https://github.com/x86dx2/x86qw/issues/48)

Este runbook governa a cerimônia inicial, renovação, rotação, revogação e
resposta a comprometimento. Ele não autoriza publicação. Comandos concretos,
provedores e identificadores só podem ser acrescentados em E2 depois da revisão
criptográfica independente.

## Papéis humanos

| Papel | Responsabilidade | Não pode fazer sozinho |
|---|---|---|
| coordenador da cerimônia | agenda, escopo, checklist e ata | assinar por outro custodian |
| custodians root (3) | proteger e usar uma chave root cada | alcançar threshold sozinho |
| autoridades targets (3) | revisar catálogo e assinar targets | preparar e aprovar a mesma promoção sozinhas |
| operadores online (2) | renovar snapshot/timestamp e monitorar expiração | alterar root ou targets |
| auditor independente | conferir software, fingerprints, artefatos e ata | operar chave de produção |
| incident commander | conter, classificar e coordenar recuperação | declarar encerramento sem auditoria |

Os nomes e contatos pertencem ao inventário confidencial de custódia. A ata
pública registra papéis e key IDs, nunca localização precisa, PIN, seed, backup
ou segredo.

## Condições de parada

Interrompa a operação, preserve a evidência e não publique `timestamp.json` se:

- baseline, issue, mudança aprovada ou conjunto de bytes não coincidirem;
- uma chave vier de fixture, checkpoint, arquivo versionado ou canal sem
  custódia comprovada;
- relógio UTC, versões monotônicas ou datas de expiração não forem verificáveis;
- houver menos assinaturas independentes que o threshold;
- um fingerprint divergir entre custodian, ata e metadata;
- qualquer mirror já contiver bytes diferentes no mesmo caminho imutável;
- revisão independente ou aprovação humana exigida estiver ausente.

Falha parcial não é autorização para reduzir threshold, estender metadata
expirada, reutilizar versão ou voltar ao catálogo sem TUF.

## Cerimônia inicial de produção

### Preparação

- [ ] ADR 0006 aprovado por maintainers, segurança e revisor independente.
- [ ] Issue/PR de E2 identifica o commit e os artefatos exatos.
- [ ] Versões e hashes de `python-tuf`, `securesystemslib` e backend
  criptográfico foram conferidos por duas pessoas.
- [ ] Ambiente offline limpo, relógio UTC, mídia e dispositivos foram
  inventariados; interfaces de rede estão desabilitadas para root/targets.
- [ ] Três custodians root e três autoridades targets estão presentes ou existe
  quorum sem compartilhar dispositivo, PIN ou material de recuperação.
- [ ] Dois operadores online e o auditor conhecem os procedimentos de expiração
  e incidente.
- [ ] O diretório de saída está vazio; nenhuma fixture ou metadata anterior será
  reutilizada.

### Geração e conferência

1. Cada custodian gera sua chave Ed25519 no dispositivo sob sua custódia usando
   somente a biblioteca aprovada. A chave privada não é exportada.
2. O coordenador coleta apenas a chave pública e o key ID calculado pela
   biblioteca. Cada custodian confere o fingerprint por um segundo canal.
3. O mesmo processo é repetido para targets. Snapshot e timestamp usam pares
   distintos; a reserva permanece selada e inativa.
4. O auditor confirma unicidade de key IDs, separação entre roles e thresholds:
   root 2-de-3, targets 2-de-3, snapshot 1-de-2 e timestamp 1-de-2.
5. A root inicial declara Ed25519, SHA-256, consistent snapshots e as janelas do
   ADR. Datas são calculadas na cerimônia e não copiadas.
6. Dois custodians root assinam os mesmos bytes. O auditor verifica o threshold
   com uma instalação limpa da biblioteca aprovada.

### Assinatura e publicação inicial

- [ ] targets autentica por tamanho e SHA-256 o catálogo exato aprovado.
- [ ] snapshot referencia a versão, tamanho e SHA-256 de targets.
- [ ] timestamp referencia a versão, tamanho e SHA-256 do snapshot.
- [ ] todos os números começam positivos e aumentam sem saltos ou reutilização.
- [ ] metadata e alvos versionados são enviados primeiro a todos os mirrors.
- [ ] cada mirror devolve bytes idênticos aos preparados localmente.
- [ ] todas as roots versionadas necessárias estão publicadas.
- [ ] `timestamp.json` é enviado por último; ele é a única troca de current.
- [ ] um cliente limpo, com a root incorporada, completa refresh e valida o
  catálogo em cada origem, um ciclo independente por origem.
- [ ] casos negativos de assinatura, expiração, rollback, freeze, equivocation,
  rotação e root não ancorada continuam falhando fechados.

A ata registra data UTC, participantes por papel, commit, versões, key IDs,
fingerprints públicos, hashes dos arquivos, resultados e aprovações. O revisor
confere que nenhum segredo entrou na ata antes de anexá-la à evidência da issue.

## Renovação normal

### Timestamp, no máximo a cada 12 horas

- [ ] monitor confirma mais de 6 horas restantes antes de iniciar;
- [ ] snapshot referenciado ainda é o último aprovado e está válido;
- [ ] versão de timestamp é exatamente a anterior mais um;
- [ ] nova expiração não passa de 24 horas;
- [ ] assinatura é verificada localmente;
- [ ] bytes são comparados em todos os mirrors;
- [ ] `timestamp.json` é promovido por último.

### Snapshot, antes de 48 horas restantes

- [ ] targets e todas as delegações referenciadas continuam presentes;
- [ ] versões nunca diminuem e hashes correspondem aos bytes publicados;
- [ ] versão de snapshot é exatamente a anterior mais um;
- [ ] nova expiração não passa de 7 dias;
- [ ] snapshot versionado é imutável nos mirrors;
- [ ] timestamp novo é preparado e promovido somente após a convergência.

### Targets, em cada promoção ou antes de 30 dias restantes

- [ ] catálogo foi aprovado e seus payloads já existem imutavelmente;
- [ ] duas autoridades independentes conferiram tamanho e SHA-256;
- [ ] versão de targets é exatamente a anterior mais um;
- [ ] nova expiração não passa de 90 dias;
- [ ] duas assinaturas targets válidas cobrem os mesmos bytes;
- [ ] snapshot e timestamp são atualizados nessa ordem.

### Root, antes de 120 dias restantes

- [ ] inventário confirma custódia e disponibilidade de 2-de-3;
- [ ] versão é exatamente `N+1`;
- [ ] bytes de `N+1` recebem threshold da root `N` e da root `N+1`;
- [ ] nova expiração não passa de 365 dias;
- [ ] `N+1.root.json` é publicado em todos os mirrors e nunca sobrescrito;
- [ ] clientes partindo de cada root ainda suportada percorrem a cadeia inteira.

## Rotação planejada

1. Abra issue privada de segurança e janela de mudança; não revogue antes de
   existir quorum substituto.
2. Gere a nova chave com o mesmo processo de custódia e confira seu fingerprint.
3. Para snapshot, timestamp ou targets, produza root `N+1` removendo a chave
   antiga e incluindo a nova. Para root, inclua o novo conjunto root.
4. Assine root `N+1` com os thresholds antigo e novo.
5. Publique a root versionada em todos os mirrors.
6. Gere novamente, conforme a role alterada, targets, snapshot e timestamp.
7. Promova `timestamp.json` por último e execute testes de cliente com cache
   antigo, cache vazio e chave revogada.
8. Sele a chave retirada, atualize o inventário e registre a destruição somente
   quando a retenção aprovada terminar.

## Resposta a comprometimento

Primeiro: pause signers e publicação, preserve logs, hashes e metadata, nomeie o
incident commander e determine a primeira janela possível de exposição. Não
apague nem sobrescreva metadata publicada.

### Timestamp comprometida

1. Rotacione a chave timestamp por root `N+1`.
2. Confira que snapshot e targets ainda correspondem ao último estado legítimo.
3. Publique nova root, snapshot se necessário e timestamp por último.
4. Teste cliente com timestamp forjado, versão inflada e timestamp expirado.

### Snapshot comprometida

1. Rotacione snapshot por root `N+1` e também timestamp se a separação de
   ambientes não puder ser provada.
2. Reconstrua snapshot a partir de targets legítimos e imutáveis.
3. Publique a cadeia nova; clientes devem descartar caches snapshot/timestamp
   depois da troca de chaves.
4. Teste mix-and-match e fast-forward com as versões observadas no incidente.

### Targets comprometida

1. Suspenda toda instalação/atualização remota.
2. Rotacione targets por root `N+1`; rotacione roles online se houver dúvida.
3. Audite todo alvo autorizado desde o início da exposição e marque artefatos
   suspeitos sem apagá-los.
4. Produza targets legítimo com versão superior, depois snapshot e timestamp.
5. Publique aviso de segurança e critérios objetivos para clientes voltarem a
   atualizar.

### Uma chave root ou um custodian comprometido

1. Com 2-de-3 ainda íntegro, produza root `N+1` que remove a chave afetada e
   adiciona uma substituta.
2. Obtenha thresholds antigo e novo, publique toda a cadeia e teste rotação a
   partir das roots antigas suportadas.
3. Investigue se targets ou signers online também foram alcançados.

### Threshold root comprometido

Pare a distribuição. A cadeia existente não pode se autorrecuperar com
segurança. Uma nova âncora exige distribuição fora da cadeia afetada, release
corretiva separada, comunicação pública e revisão externa. Não apresente a nova
root como rotação normal.

## Rollback, freeze e equivocation observados

- capture URL redigida, versão, expiração, tamanho, hashes e mirror;
- repita somente com cliente isolado e sem alterar o cache original;
- compare os mesmos caminhos nos demais mirrors, sempre em ciclos separados;
- não aceite a maior versão automaticamente: versão inflada pode ser
  fast-forward;
- preserve a última metadata confiável, bloqueie mutação remota e escale como
  incidente até excluir comprometimento;
- restaure disponibilidade apenas por nova cadeia assinada e monotônica.

## Evidência de encerramento

- [ ] causa, janela de exposição e roles afetadas estão registradas;
- [ ] key IDs revogados e substitutos aparecem na root correta;
- [ ] thresholds antigo e novo foram verificados;
- [ ] todos os mirrors servem bytes idênticos;
- [ ] testes adversariais reproduzem o ataque e passam após a correção;
- [ ] clientes com cache anterior e clientes limpos atualizam com segurança;
- [ ] nenhuma chave privada, PIN, seed, token ou localização sensível entrou na
  evidência;
- [ ] auditor independente e autoridade humana aprovaram o encerramento.

## Gate de aprovação E1

- [ ] maintainers aprovam TUF, Ed25519 e `python-tuf`/`securesystemslib`;
- [ ] segurança aprova thresholds, custódia e janelas de expiração;
- [ ] operações aceita o SLA de timestamp e os alertas;
- [ ] revisão criptográfica independente não possui achado bloqueador;
- [ ] plano de clientes legados elimina fallbacks para `main` mutável;
- [ ] todos reconhecem que E1 não cria nem promove trust de produção.

Somente depois desse checklist E2 pode escrever testes RED e implementar a
cadeia. A primeira chave de produção continua bloqueada até a cerimônia inicial.

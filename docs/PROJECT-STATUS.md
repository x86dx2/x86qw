# Estado atual do projeto

## Baseline real

`origin/main@d4a92c0fe29786fdc6ec5c7d978813cb634be62c` é a main pública
observada nesta auditoria. O checkout local desta branch é
`codex/stabilize-1.0@30e9d5bd4f4032772c63d37666ebdedbb33fc292`; ele contém as
correções locais ainda não publicadas e não deve ser confundido com `main`.

## Versão pública

O último release público confirmado no GitHub continua sendo `0.7.13`: instalador
SHA-256 `114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`,
581883 bytes. O catálogo público não pôde ser revalidado: os seis endpoints do
portal expiraram por timeout HTTPS no checkpoint atual. O checkout local ainda
projeta o histórico `0.7.3` e não altera esses bytes públicos.

## Trust

O runtime local usa TUF padrão com root Ed25519 incorporada. Na última coleta
pública, a cadeia observada estava em root v1 e targets/snapshot/timestamp v11;
o timestamp observado expirava em `2026-08-11T23:10:15Z` e a root em 2027-08-10
UTC. Esse timestamp já está expirado no checkpoint atual, e a revalidação não
pôde ser concluída porque o endpoint não aceita conexão. Isso prova apenas a
validação técnica daquele snapshot histórico, não renovação operacional,
custódia humana independente ou recuperação após expiração.

## Implementado neste checkpoint

- downloader, archives, SemVer, launchers, `changes` e `migrate` compartilham
  contratos runtime;
- fixtures reais dos instaladores públicos `0.7.0`–`0.7.13` foram adicionadas à
  migração;
- `verify` autentica TUF padrão localmente e permanece pendente quando a
  projeção pública não está no checkout;
- publisher não reconstrói artefatos ausentes;
- workflows de validação e release têm build-once, transporte por artifact,
  approval, mirrors e metadata-last fail-closed; o workflow separado
  `.github/workflows/native-m3.yml` executa o candidato em runner Apple M3;
- o monitor público TUF autenticado e agendado foi adicionado, sem fingir que
  ele é um signer ou uma cerimônia de renovação;
- o harness M3 exige plano e evidência observável, sem fabricar aprovação;
- o smoke QWFWD aguarda uma resposta encaminhada após o handshake remoto, evitando
  perder o primeiro datagrama por uma corrida de estado;
- o candidato `live6` executou 18/18 casos no Apple M3, e os gates locais fecharam
  com 1.642 testes de manutenção (38 skips esperados) e 6 testes do site, tanto
  no Python 3.11 quanto no runtime local mais novo;
- o candidato e o handoff M3 agora são transportados por IDs de artifact e
  revalidados por um binding de hashes antes da aprovação/publicação;
- o candidato agora contém o site público renderizado a partir dos bytes do
  checkout e dos bootstraps de `dist`; TUF stale é removido do staging e
  rejeitado pelo `release_candidate` antes do transporte;
- `dist/installer/VERSION` ainda é `0.7.3` no checkout de desenvolvimento; o
  `live6` foi gerado explicitamente como `1.0.0-rc.1`, portanto não é uma
  promoção implícita da versão-fonte nem pode ser publicado sem um commit de
  release coerente;
- o workflow M3 agora gera também o registro de plataforma e o corpo canônico;
  o workflow protegido `sign-native-evidence.yml` recebe somente assinaturas
  públicas externas, autentica a root e monta `release-evidence.json` sem
  manipular chave privada;
- o hashing de artefatos grandes usa o limite de arquivo-fonte do archive,
  sem ampliar o limite menor aplicado a membros gerenciados;
- a instalação limpa no macOS não tenta importar um domínio de preferências
  vazio, e o `uninstall --purge` remove nós não regulares somente sob uma
  transação explícita e vinculada por identidade;
- os launchers agora têm um gate exato contra `capabilities.json` e anunciam
  `changes --sync-gitignore` e `migrate --dry-run` no help;

## Bloqueios atuais

1. o candidato local atual `1.0.0-rc.1` foi reconstruído a partir de `dist` e
   executou 18/18 casos no M3; o manifest SHA é
   `688cdf2da203d2f00767da98b6cadeaff22dfb730795ff9841ada0660e0bac0b` e o
   instalador tem 600039 bytes e SHA-256
   `237be02f65451147c7d94ea03fc8eeb5fdcb8e8839ac9aee260e78ad4fafd975`, mas a
   evidência é `pending`, não assinada e não foi anexada a uma release pública;
2. o timestamp TUF público observado expirou e não há
   signer/custódia/alerta externo de produção demonstrados; o monitor
   versionado falhará fechado enquanto o endpoint estiver indisponível;
3. o token GitHub local está inválido, então o gate protegido não pode consultar
   issues, anexar evidência ou publicar;
4. o catálogo local e os bytes públicos estão em gerações diferentes;
5. a promoção exige configurar o plano M3 e a custódia de metadata no ambiente
   protegido; nenhuma dessas entradas é inferida.

O último `Validate` público da `main` (run
`31442335177`, job `93629452223`, em 2026-08-10) também falhou no job
Windows/Python 3.10
(`test_macos_launch_target_binds_every_bundle_directory`): o runner recebeu a
falha segura `Alvo ... ausente ou inseguro` ao revalidar o bundle substituído,
enquanto o teste aceitava somente `mudou`. O checkpoint local ampliou a
asserção para aceitar as duas formas de rejeição segura e a suíte local passou;
isso ainda não corrige a `main` até ser publicado.

As correções acima são verificadas somente no checkout local desta branch. Elas
não alteram retroativamente o instalador público `0.7.13` nem seus mirrors.

Portanto: nova `0.7.x` somente para regressão crítica da versão pública; RC e
`1.0.0` permanecem NO-GO até fechar os bloqueios acima. Linux, Windows e macOS
Intel continuam preview, sem alegação de execução nativa.

## Próxima ação

Restaurar primeiro a disponibilidade de `x86qw.x86.com.br` e a operação da
metadata TUF; depois autenticar o GitHub, executar o par
`.github/workflows/native-m3.yml` → `.github/workflows/sign-native-evidence.yml`
sobre o artifact exato, com envelope assinado pelo custodiante e root pública
operacional. Só então iniciar o workflow de promoção e registrar a cerimônia
TUF.

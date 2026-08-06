# ADR 0006 — Trust metadata para catálogo e release

- **Estado:** implementado localmente; não publicado
- **Data:** 2026-08-04
- **Issue:** #48
- **Escopo:** autenticar o catálogo e os metadados públicos sem alterar os
  catálogos/release existentes da série 0.7.x

## Contexto

Os campos `sha256` que aparecem dentro de `site/public/api/v1/catalog.json`
descrevem os artefatos, mas são dados fornecidos pelo próprio catálogo. Um
mirror que consiga trocar o catálogo também consegue trocar esses hashes. Eles
não são uma autenticação. O cliente precisa de uma raiz fixada, versões
monotônicas, expiração e uma cadeia assinada que seja independente do mirror.

O runtime instalado precisa continuar sendo biblioteca-padrão. Python 3.10 não
oferece Ed25519, RSA-PSS ou outra verificação assimétrica no módulo `hashlib`.
Adicionar uma dependência criptográfica neste recorte mudaria o empacotamento e
exigiria uma decisão separada. A implementação local, portanto, contém somente
um verificador mínimo de RSA-PSS-SHA256 (RFC 8017), sem operação de chave
privada. Ele requer revisão criptográfica independente e vetores cruzados com
OpenSSL antes de ser considerado adequado para uma chave de produção.

## Decisão

`x86qw_runtime/trust.py` é a fronteira canônica. Ela recebe bytes já limitados,
o relógio UTC e os `TrustedVersions` persistidos pelo chamador. Não faz rede,
filesystem, atualização de estado, import de `maintenance` nem gravação. O
chamador deve persistir o resultado somente depois da validação completa.

O formato é TUF-like, com envelope exato:

```json
{"signatures":[{"keyid":"…","sig":"base64url sem padding"}],"signed":{}}
```

Somente o objeto `signed` é assinado em JSON canônico UTF-8
(`sort_keys=True`, separadores compactos, NFC, sem floats, chaves duplicadas ou
valores não finitos). `keyid` é SHA-256 do objeto público canônico. A chave RSA
usa `keyval.public.n` em hexadecimal e `keyval.public.e` como inteiro; o
modulus precisa ter pelo menos 3072 bits. O PSS usa SHA-256, MGF1-SHA256 e salt
fixo de 32 bytes, comparando o digest com `hmac.compare_digest`.

`release-evidence.json` usa o mesmo princípio: a forma canônica remove o campo
`signatures` antes da verificação e aceita uma lista fechada de signatários
distintos. Uma evidência antiga com o campo singular `signature` só é aceita
quando o papel `evidence` tem threshold `1`; ela não reduz um threshold maior.
O root inicial precisa conter e também apresentar uma assinatura válida da
`ROOT_KEY_ID` fixada — declarar essa chave no papel sem sua assinatura não é
âncora suficiente.

Os papéis são separados:

| Papel | Conteúdo | Proteção principal |
| --- | --- | --- |
| `root` | chaves e thresholds | raiz offline, rotação contígua e dupla assinatura |
| `current` | ponteiro curto para um snapshot | anti-freeze e versão monotônica |
| `snapshot` | digest/tamanho/versão do catálogo | anti-rollback e binding do catálogo |
| `evidence` | reservado para `release-evidence.json` | nunca reutilizar a chave de `current` |

O root inicial é `maintenance/inventory/trust/root.json`; a chave pública
correspondente fica fixada em `ROOT_PUBLIC_KEY`/`ROOT_KEY_ID` na CLI/runtime.
Os artefatos canônicos são:

- `maintenance/inventory/trust/current.json`;
- `maintenance/inventory/trust/snapshot.json`;
- `site/public/api/v1/catalog.json` (somente referenciado, sem alteração).

`current` expira em no máximo sete dias e aponta exatamente para o tamanho,
digest e versão de `snapshot.json`. `snapshot` aponta exatamente para o
tamanho, digest e versão do catálogo público. URLs, caminhos absolutos,
backtracking, barras invertidas e campos extras são rejeitados. Assim, os
hashes continuam importantes para binding e detecção de corrupção, mas só são
aceitos depois da assinatura do papel que os contém.

### Integração no cliente instalado

O cliente instalado mantém a compatibilidade com releases antigos: sem
metadados locais, sem URLs de trust configuradas e sem estado persistido, o
fluxo legado do catálogo continua disponível. Quando o pacote local contém o
conjunto completo `maintenance/inventory/trust/{root,current,snapshot}.json`,
ele é verificado antes de o catálogo ser usado. Para catálogo remoto, a mesma
proteção é ativada por `X86_QW_TRUST_METADATA_URL` (base que contém os três
arquivos) ou pelas URLs explícitas `X86_QW_TRUST_{ROOT,CURRENT,SNAPSHOT}_URL`.

`X86_QW_TRUST_METADATA_REQUIRED=1` (também aceito como
`X86_QW_REQUIRE_TRUST_METADATA=1`) transforma a ausência de uma fonte de
metadados em erro; falha de assinatura, expiração, rollback, rotação ou digest
sempre falha fechado quando uma fonte é encontrada. Root/current/snapshot
remotos são baixados e validados antes do pedido do catálogo, e o catálogo só é
aceito depois da verificação final do digest e tamanho assinados.

Depois de uma verificação bem-sucedida, o cliente grava atomicamente
`.x86qw/trust/versions.json` com modo privado. O formato `2` contém as versões,
digests, o envelope público do root anterior e, quando houver evidência
promovida, o par `evidence_version`/`evidence_digest`. Nenhuma chave privada ou
segredo é persistido. O leitor importa explicitamente o formato legado `1`
(sem estado de evidence) e sempre emite o formato `2`; campos extras ou uma
mistura implícita dos dois contratos falham fechado. Se esse estado já existe,
a ausência de metadados atualizados não permite voltar silenciosamente ao
fluxo sem autenticação.

Antes de apontar essas URLs para produção, a equipe de release precisa decidir
humanamente o endpoint/mirror oficial, revisar a implementação RSA-PSS e
aprovar a chave offline. Esta integração local não altera
`site/public/api/v1/catalog.json`, os bootstraps públicos ou
`dist/installer/VERSION`, e não constitui autorização de publicação.

### Gate de confiança oficial

O endpoint oficial, o conjunto de mirrors e a chave pública de produção ainda
não existem neste checkout de estabilização. `ROOT_PUBLIC_KEY` e os arquivos em
`maintenance/inventory/trust/` são fixtures públicas para vetores locais; não
devem ser promovidos, trocados por uma chave inventada ou tratados como a
âncora operacional da próxima release. O verificador recebe bytes já obtidos e
não faz descoberta de endpoint, rotação automática, publicação ou custódia de
chave privada.

Habilitar confiança oficial é um gate externo: a autoridade de release precisa
escolher um endpoint HTTPS imutável, custodiar a chave offline, definir os
signatários/thresholds de `root`, `current`, `snapshot` e `evidence`, executar a
revisão criptográfica independente e registrar a cerimônia. Só depois dessa
aprovação o chamador instalado poderá apontar as URLs de trust para produção e
substituir a âncora fixture em uma mudança explícita. Até lá, a compatibilidade
legada permanece limitada aos bundles públicos 0.7.3 sem um conjunto local
completo de metadados; se qualquer parte do conjunto estiver presente, o fluxo
local falha fechado, e um estado persistido impede voltar silenciosamente ao
catálogo sem autenticação.

Uma versão menor que a persistida é rollback. A mesma versão sem o mesmo digest
é equivocation e também falha. A rotação de root `N -> N+1` exige a threshold
da raiz antiga e a threshold da nova; saltar uma versão, remover uma chave sem
essa transição ou usar um papel revogado falha fechado. A evidência aplica a
mesma regra à precedência SemVer: uma versão menor é rollback e a mesma versão
com digest canônico diferente é equivocation. Sua identidade esperada é
obrigatória e deve casar com `version`/`commit` do topo, `candidate.version`,
`candidate.commit` e o SHA-256 do manifest. O estado de versões e o root
confiável anterior pertencem ao chamador e devem ser gravados atomicamente.
`verify_release_evidence` devolve o documento como um mapping compatível e,
junto, o `TrustedVersions` já atualizado com o root (versão, digest e envelope
público) e com `evidence_version`/`evidence_digest`; isso mantém a cadeia de
rotação mesmo quando o estado persistido começou como evidence-only. O chamador
deve persistir esse checkpoint, não reconstruí-lo a partir da versão anterior.

## Runbook de comprometimento/rotação

1. Interromper promoção e publicação; não substituir catálogo, snapshot ou
   ponteiro existentes.
2. Preservar os bytes recebidos, logs e digests como evidência. Não registrar a
   chave privada, senha ou material de seed em argv, journal ou ticket.
3. Revogar o papel comprometido no novo `root` (`N+1`), gerar uma chave fora do
   workspace e obter as thresholds da raiz antiga e nova. Se a raiz antiga
   estiver indisponível, tratar como recuperação manual de confiança e não
   como update automático.
4. Publicar primeiro os novos assets imutáveis; depois `snapshot`; por último
   `current`. Nunca publicar um ponteiro que anteceda os assets.
5. Invalidar caches/mirrors que contenham a versão comprometida e conferir
   tamanho e digest em cada mirror.
6. Rodar a validação da cadeia em ambiente limpo e registrar a revisão
   criptográfica independente, os vetores OpenSSL e a aprovação humana antes
   de reabrir a promoção.

As chaves privadas de produção permanecem offline e não são fornecidas pelo
runtime. Os arquivos locais e os fixtures desta PR usam uma chave de teste
offline para permitir verificação reproduzível; isso não é evidência de uma
chave de publicação de produção.

## Consequências e limites

- A cadeia autentica conteúdo e metadados sem confiar em hashes fornecidos pelo
  próprio catálogo.
- O relógio local ainda é uma dependência para expiração/anti-freeze; um host
  com relógio comprometido precisa de uma política externa de tempo.
- A implementação RSA-PSS é uma superfície criptográfica própria. Antes da
  versão 1.0, deve haver revisão independente e cross-vector. Se o gate não for
  aprovado, substituir a fronteira por uma biblioteca mantida (por exemplo,
  Python-TUF/cryptography) em uma mudança de dependência explícita.
- Persistência de `TrustedVersions`, downloader, publicação de assets e
  `release-evidence.json` pertencem às PRs/owners correspondentes; este ADR não
  simula esses caminhos nem autoriza publicação.

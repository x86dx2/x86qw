# Threat model resumido

## Ativos

- catálogo, pacotes e metadados TUF publicados;
- chaves TUF (root, targets, snapshot e timestamp);
- configurações pessoais e senhas dos serviços;
- estado, recibos, logs, demos e PAKs do usuário;
- a CLI e os executáveis nativos instalados.

## Adversários

- mirror comprometido ou resposta remota maliciosa;
- ZIP ou PK3 malformado;
- processo concorrente tentando trocar um arquivo durante a validação;
- usuário sem privilégio ou processo local não relacionado;
- metadados antigos reapresentados (rollback ou freeze);
- falha abrupta durante uma mudança ou o encerramento de um serviço.

## Controles

Downloads exigem HTTPS, limites, deadline, tamanho e SHA-256; arquivos são
baixados em staging privado e promovidos atomicamente. A fronteira de arquivo
percorre o ZIP inteiro antes de extrair e rejeita traversal, links, tipos
especiais, colisões e limites de expansão. Objetos Windows sensíveis usam DACL
protegida.

Sessões possuem lock por instalação, identidade de controlador e journal; a
recuperação é conservadora e nunca encerra um PID sem correspondência de token
e executável. Senhas não entram em argv, logs, journals ou mensagens.

O catálogo só é aceito autenticado por TUF. A CLI embute a root (v1), com
chaves Ed25519 e quatro papéis: root e targets com threshold 2 de 3, snapshot e
timestamp com threshold 1 de 2. Ela verifica assinaturas, thresholds, expiração
e versões monotônicas, guarda os metadados confiáveis no cache e rejeita
rollback e um timestamp que reutilize uma versão com outros bytes. O targets
autoriza um único alvo, o catálogo, por tamanho e SHA-256; o bootstrap fixa a
CLI por tamanho e SHA-256, e cada pacote do catálogo também.

Os bootstraps são servidos por HTTPS. O de Unix só executa o corpo na última
linha, então um download truncado não roda nada; o de Windows confere tamanho e
SHA-256 do binário antes de executá-lo.

## Limites

- As chaves offline ficam num único Mac do mantenedor; o modelo não cobre a
  perda ou o comprometimento dessa máquina além do
  [runbook de comprometimento](runbooks/trust-compromise.md).
- A chave online do timestamp vive num segredo do GitHub; quem a obtém pode
  estender a validade da geração publicada, mas não mudar o catálogo.
- O modelo não cobre licenciamento ou redistribuição dos PAKs.
- Validação nativa que não foi executada numa plataforma não pode ser inferida
  dos testes de CI; a alegação pública deve ser reduzida ou continuar
  condicional.

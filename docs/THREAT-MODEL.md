# Threat model resumido

## Ativos

- payloads e metadados de release;
- chaves de trust e evidência;
- configurações pessoais e segredos de serviços;
- estado, recibos, logs, demos e PAKs do usuário;
- executáveis e bundles nativos.

## Adversários

- mirror comprometido ou resposta remota maliciosa;
- ZIP/PK3/PYZ malformado;
- processo concorrente tentando trocar um arquivo durante a validação;
- usuário sem privilégio ou processo local não relacionado;
- release antiga reutilizada por rollback/freeze;
- falha abrupta durante mutação ou encerramento de serviço.

## Controles

Downloads exigem HTTPS, limites, deadline, tamanho e digest; arquivos são
baixados em staging privado e promovidos atomicamente. A fronteira de arquivo
faz preflight completo e rejeita traversal, links, tipos especiais, colisões e
limites de expansão. Objetos Windows sensíveis usam DACL protegida.

Sessões possuem lock por instalação, identidade de controlador e journal; a
recuperação é conservadora e nunca encerra PID sem correspondência de token e
executável. Segredos não entram em argv, logs, journals ou mensagens.

Para um candidato 1.0 futuro, os metadados de release deverão ser validados por
raiz fixada, assinatura RSA-PSS, threshold, expiração, versão monotônica e
papéis separados (`root`, `snapshot`, `current` e `evidence`). A evidência
deverá exigir uma identidade de candidato esperada, rejeitar
rollback/equivocation por versão SemVer e digest canônico, e exigir que o root
inicial seja assinado pela chave fixada. Chaves privadas ficam fora do
repositório. O baseline 0.7.3 não habilita essa cadeia de trust nem contém
chave de produção; nenhum desses controles deve ser inferido como executado.

## Limites

O modelo não cobre licenciamento ou redistribuição dos PAKs. Smokes nativos que
não forem executados em uma plataforma não podem ser inferidos pela matriz
portável; a alegação pública deve ser reduzida ou permanecer condicional.

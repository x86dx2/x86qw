# Runbook de release

Este checkout é validado e operado diretamente no Mac para a release pública
0.7.3. Linux-X64, Windows-X64, macOS-ARM64 e macOS-X64 continuam nomes de
compatibilidade/catalogo; nenhuma dessas plataformas é executada ou usada como
pré-condição da promoção local sem evidência nativa correspondente.

## Fluxo local

Para uma validação local, a sequência mínima é:

```sh
set -eu

git lfs pull
git lfs fsck
PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify
PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py build
```

Esses comandos validam os catálogos, builds e testes do baseline. Eles não
criam automaticamente `candidate.json`, `ownership.json`, SBOM, provenance ou
evidência nativa; esses artefatos pertencem a um candidato 1.0 futuro e exigem
um procedimento versionado próprio.

Não copie metadata de outro candidato nem altere o commit registrado. Qualquer
montagem de candidato futuro deve conter exatamente os artefatos que serão
verificados e promovidos.

## Compatibilidade opcional

`release-evidence.json`, `trust_root`, handoffs externos, metadata assinada,
mirrors e ferramentas adicionais de publicação exigem uma decisão explícita
futura. Nenhum arquivo de evidência nativa transforma um candidato Mac em
afirmação de validação multiplataforma.

Quando uma publicação remota for autorizada separadamente, o conjunto público
deverá incluir os ZIPs e os documentos auditáveis definidos pelo candidato
aprovado. A publicação remota é uma operação posterior, opcional e fora da
validação local; este runbook não executa upload, criação de release, alteração
de catálogo ou uso de chave privada.

Depois de uma publicação autorizada, uma verificação pública de instalação no
Mac pode ser executada conforme a ferramenta disponível no candidato. Isso não
é smoke nativo de runtime e não substitui evidência de outras plataformas.

A release `0.7.3` permanece imutável. A `1.0.0` só poderá ser publicada depois
dos controles de ownership, trust e aprovação que forem explicitamente
autorizados para a operação remota; a ausência de smokes Linux/Windows mantém
as alegações dessas plataformas condicionais.

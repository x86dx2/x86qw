# Roteiro

## 0. Fundação

- [x] criar o repositório local separado do protótipo atual;
- [x] definir o contrato mínimo do catálogo;
- [x] bloquear PAKs comerciais e artefatos gerados no Git;
- [x] registrar a política de proveniência.

## 1. Ingestão reproduzível

- [ ] confirmar licença e redistribuição de cada componente;
- [x] criar receitas fixadas por versão, origem e checksum;
- [x] baixar em uma área temporária e validar formatos;
- [x] registrar artefatos revisados de forma atômica no catálogo;
- [x] gerar mirrors x86QW byte a byte a partir das mesmas entradas;
- [x] preservar localmente somente releases/nightlies ezQuake e arquivos nQuake consumidos;
- [x] decompor o conteúdo de referência nQuake em BOM de 17 componentes sem overlays inúteis;
- [x] gerar pacotes determinísticos por componente a partir do snapshot validado.
- [x] registrar estratégia, versão e upstream separadamente para todos os componentes;
- [x] detectar atualizações upstream sem publicá-las automaticamente;
- [x] atualizar KTX de `1.46-dev` para `1.47` preservando os recursos nQuake.
- [x] incorporar TD2QW 2.22 como pacote independente, preservando o original e sem mapas adicionais.
- [x] separar fisicamente nQuake, KTX e TD2 por origem e generalizar o pipeline de componentes.

## 2. Distribuição

- [x] criar `x86dx2/x86qw` no GitHub;
- [x] criar `x86dx2/x86qw-dist` no GitHub e GitLab;
- [x] publicar e verificar os 24 artefatos instaláveis no GitLab Generic Package Registry;
- [x] publicar a primeira release nQuake imutável e seus checksums;
- [x] manter R2 fora da arquitetura desta fase.

## 3. Instalador

- [x] estabilizar e testar o instalador existente em `../quake`;
- [x] migrar a base funcional e suas duas suítes de regressão;
- [x] limitar o cliente ativo ao ezQuake stable/nightly;
- [x] manter stable e nightly coexistentes em macOS, Linux e Windows;
- [x] oferecer perfis e seleção individual dos 18 componentes;
- [x] registrar recibo e inventário independentes por componente;
- [x] fazer o instalador consumir pacotes x86QW do `x86qw-dist`, sem depender dos repositórios upstream;
- [x] mostrar versão atual e notas de release antes de atualizar componentes;

## 4. Site

- [x] configurar Workers Static Assets no monorepo;
- [x] construir a página de divulgação responsiva e acessível;
- [x] documentar produto e sistema visual;
- [x] publicar o site com Cloudflare Workers Static Assets;
- [x] expor o catálogo canônico em `/api/v1/catalog.json`;
- [ ] redirecionar `x86.com.br/x86qw` para `x86qw.x86.com.br`.

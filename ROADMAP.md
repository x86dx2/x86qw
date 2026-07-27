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
- [x] decompor o conteúdo nQuake em BOM de 17 componentes sem overlays inúteis;
- [x] gerar pacotes determinísticos por componente a partir do snapshot validado.

## 2. Distribuição

- [x] criar `x86dx2/x86qw` no GitHub;
- [x] criar `x86dx2/x86qw-dist` no GitHub e GitLab;
- [ ] automatizar o mirror passivo no GitLab (repositório e cópia inicial criados);
- [x] publicar a primeira release nQuake imutável e seus checksums;
- [x] manter R2 fora da arquitetura desta fase.

## 3. Instalador

- [x] estabilizar e testar o instalador existente em `../quake`;
- [x] migrar a base funcional e suas duas suítes de regressão;
- [x] limitar o cliente ativo ao ezQuake stable/nightly;
- [x] manter stable e nightly coexistentes em macOS, Linux e Windows;
- [x] oferecer perfis e seleção individual dos 17 componentes nQuake;
- [x] registrar recibo e inventário independentes por componente;
- [x] fazer o instalador consumir pacotes nQuake do `x86qw-dist`, sem depender do repositório upstream;

## 4. Site

- [x] configurar Workers Static Assets no monorepo;
- [x] construir a página de divulgação responsiva e acessível;
- [x] documentar produto e sistema visual;
- [x] publicar o site com Cloudflare Workers Static Assets;
- [x] expor o catálogo canônico em `/api/v1/catalog.json`;
- [ ] redirecionar `x86.com.br/x86qw` para `x86qw.x86.com.br`.

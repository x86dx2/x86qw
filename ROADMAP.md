# Roteiro

## 0. Fundação

- [x] criar o repositório local separado do protótipo atual;
- [x] definir o contrato mínimo do catálogo;
- [x] bloquear PAKs comerciais e artefatos gerados no Git;
- [x] registrar a política de proveniência.

## 1. Ingestão reproduzível

- [ ] confirmar licença e redistribuição de cada componente;
- [ ] criar receitas fixadas por versão, origem e checksum;
- [ ] baixar em uma área temporária e validar formatos;
- [ ] gerar pacotes x86QW idênticos a partir das mesmas entradas.

## 2. Distribuição

- [ ] criar `x86dx2/x86qw` no GitHub;
- [ ] configurar o mirror passivo no GitLab;
- [ ] publicar releases imutáveis e seus checksums;
- [ ] avaliar R2 para `downloads.x86.com.br/x86qw/`.

## 3. Instalador

- [ ] estabilizar e testar o instalador existente em `../quake`;
- [ ] migrá-lo para este repositório preservando o histórico relevante;
- [ ] substituir consultas diretas a terceiros pelo catálogo x86QW;
- [ ] manter stable e nightly coexistentes em macOS, Linux e Windows.

## 4. Site

- [ ] criar o repositório separado `x86dx2/x86qw-site`;
- [ ] publicar o site com Cloudflare Workers Static Assets;
- [ ] servir o catálogo em `/api/v1/catalog.json`;
- [ ] redirecionar `x86.com.br/x86qw` para `x86qw.x86.com.br`.

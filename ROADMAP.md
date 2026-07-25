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
- [x] registrar artefatos revisados de forma atômica no catálogo;
- [ ] gerar pacotes x86QW idênticos a partir das mesmas entradas.

## 2. Distribuição

- [ ] criar `x86dx2/x86qw` no GitHub;
- [ ] configurar o mirror passivo no GitLab;
- [ ] publicar releases imutáveis e seus checksums;
- [ ] avaliar R2 para `downloads.x86.com.br/x86qw/`.

## 3. Instalador

- [x] estabilizar e testar o instalador existente em `../quake`;
- [x] migrar a base funcional e suas duas suítes de regressão;
- [x] usar o catálogo x86QW para ezQuake e clientes alternativos;
- [ ] criar pacotes próprios para dados nQuake, mapas e LOCs aprovados;
- [ ] manter stable e nightly coexistentes em macOS, Linux e Windows.

## 4. Site

- [x] configurar Workers Static Assets no monorepo;
- [ ] publicar o site com Cloudflare Workers Static Assets;
- [x] expor o catálogo canônico em `/api/v1/catalog.json`;
- [ ] redirecionar `x86.com.br/x86qw` para `x86qw.x86.com.br`.

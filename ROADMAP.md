# Roteiro técnico x86QW

Este roteiro substitui integralmente o anterior. Ele consolida a auditoria de 30 de julho de 2026, a reconstrução dos mods e as pendências para uma distribuição pública. A regra desta fase é estrita: preservar o jogo original e corrigir compatibilidade, integração, desempenho operacional e reprodutibilidade; não criar modos, mecânicas, mapas ou recursos novos.

Legenda: `[x]` concluído e verificado; `[-]` decisão permanente ou fora do escopo atual; `[ ]` trabalho pendente.

## 0. Contrato da distribuição

- [x] manter `dist/` como fonte canônica de tudo que é instalado ou consultado no desenvolvimento;
- [x] centralizar código, artefatos e releases em `x86dx2/x86qw`, sem depender de `x86qw-dist` ou R2;
- [x] usar Git LFS para binários e manter metadados, configurações, patches e changelogs revisáveis;
- [x] aceitar somente conteúdo com consumidor explícito no x86QW;
- [x] preservar upstreams originais imutáveis e aplicar a camada x86QW separadamente;
- [x] manter stable e nightly como clientes oficiais coexistentes, com configuração comum limitada ao denominador compatível;
- [-] não ativar recursos exclusivos de nightly na configuração global;
- [-] não modernizar QuakeC nem criar recursos novos nesta fase;
- [-] não baixar coleções globais de mapas, LOCs ou GFX; incorporar apenas itens escolhidos e consumidos;
- [-] não adicionar FortressOne nem outro runtime nesta fase.

## 1. Proveniência e reconstrução dos mods

### KTX 1.47

- [x] preservar a fonte oficial `ktx-1.47.tar.gz`, o QVM e o mapa de símbolos oficiais;
- [x] compor `nQuake -> KTX oficial -> x86QW`, mantendo somente recursos nQuake que ainda agregam valor;
- [x] confirmar que `1.47` é a última release oficial; `1.48-dev` não substitui uma release na distribuição;
- [x] manter bots, LOCs, configurações, sons e recursos oficiais presentes no pacote 1.47;
- [x] corrigir o primeiro carregamento local para definir `k_defmap` antes do mapa e eliminar a troca `dm6 -> dm3` e o aviso `SV_PreSpawn_f from different level`;
- [-] não criar menu x86QW de modos KTX: seria recurso novo.

### Final Arena 1.20

- [x] localizar e preservar o servidor oficial `fasrv12.zip`;
- [x] localizar e preservar o cliente oficial `farena12.zip`;
- [x] extrair e preservar separadamente a fonte QuakeWorld oficial `qwrasrc12.zip`;
- [x] comprovar que os 93 membros do `pak0.pak` oficial estão byte a byte no conteúdo nQuake;
- [x] identificar o `qwprogs.dat` nQuake como derivação de Rakk, com votação, mochilas, airgib e estatísticas adicionais;
- [x] buscar a fonte da derivação de Rakk em repositórios e arquivos públicos;
- [-] manter temporariamente o binário de Rakk porque a fonte correspondente não foi localizada; não reimplementar suas alterações por inferência;
- [-] não adicionar `sprites/s_aball.spr`: a referência ocorre apenas em `amtest.qc`, entidade de teste fora do cliente oficial;
- [ ] criar build-base reproduzível da fonte 1.20 oficial e comparar seu comportamento com o binário oficial;
- [ ] separar uma eventual migração do binário Rakk somente após paridade comprovada de fonte e runtime.

### Pro-X 1.1

- [x] preservar o pacote completo original `prox_11.zip`, incluindo seis mapas, mídia, gamecode e ENT;
- [x] confirmar no anúncio do autor que a fonte não foi publicada por decisão explícita;
- [-] preservar o binário original e limitar mudanças a dados/configuração; não decompilar nem reimplementar;
- [x] gerar `proxmap1.ent` a partir do lump original removendo apenas quatro pares obsoletos `"noname" "1"` que causavam erros no console;
- [-] não adicionar `q1edge.bsp` nem `q1_q3dm13.bsp`: são arenas externas opcionais, não dependências dos seis mapas originais.

### Team Fortress 2.9

- [x] preservar o cliente completo oficial 2.8 (`tf28.zip`), necessário porque 2.9 foi publicado como atualização apenas de servidor;
- [x] preservar a atualização QuakeWorld 2.9 (`tf29qw.zip`) e a fonte oficial (`tf_29src.zip`);
- [x] preservar os assets e mapas úteis do nQuake e substituir somente o gamecode 2.8 pelo 2.9;
- [x] localizar `weapons/bounce2.wav` oficial, referenciado pelo QuakeC mas ausente nos pacotes TF, dentro dos recursos KTX 1.47, com hash registrado;
- [x] compilar uma camada de compatibilidade removendo apenas binds remotos forçados; os binds passam a ser propriedade do perfil local x86QW;
- [x] reduzir `cl_remote_capabilities` ao conjunto realmente exigido por velocidade de classe e efeitos, sem conceder `bind`;
- [x] testar o gamecode recompilado contra o oficial 2.9 em stable e nightly antes de promovê-lo;
- [-] manter `clan1.wav` a `clan5.wav` como extensão administrativa opcional não resolvida: não aparecem nos pacotes oficiais e só são usados quando `localinfo clanmsgs on`;
- [-] não inventar sons substitutos para `clan1.wav` a `clan5.wav`.

### Total Destruction 2 2.22

- [x] preservar a distribuição completa original com fonte, manuais, gamecode, modelos e sons;
- [x] restaurar `saw_down.wav` a partir de `saw.wav`, comprovadamente byte-idêntico;
- [x] preparar patch QuakeC apenas de compatibilidade: restaurar o campo padrão `wad`, remover controle forçado de demos, binds, `scr_centertime`, gamma persistente e a referência morta a `vomitus/v_sight1.wav`;
- [x] preservar o efeito da bomba de luz com `EF_BRIGHTLIGHT`/`EF_DIMLIGHT`, sem sobrescrever a preferência de gamma do jogador;
- [x] recompilar o patch com FTEQCC preservando o arquivo upstream intacto;
- [x] testar gamecode recompilado em stable e nightly antes de promovê-lo;
- [-] não adicionar o som `vomitus/v_sight1.wav`: a referência morta já existia no Quake 1.06 e não há arquivo original verificável;
- [-] não baixar os 30 mapas externos codificados na votação; documentá-los como opcionais e manter a política de mapas escolhidos;
- [-] não criar menu de modos nem alterar `temp1 65560`, que continua selecionando pipeheads, luz e revenants como antes.

## 2. Fontes e builds reproduzíveis

- [x] manter os arquivos upstream originais imutáveis em `source/` ou `upstream/`;
- [x] manter patches x86QW em `x86qw/source/` e binários promovidos em `x86qw/runtime/`;
- [x] registrar hash, tamanho, origem e finalidade de cada arquivo reconstruído;
- [ ] implementar a cadeia `fonte imutável -> normalização declarada -> patches x86QW -> toolchain fixada -> build determinístico -> comparação de baseline -> smoke stable/nightly -> promoção`;
- [ ] adicionar `maintenance/manage.py source-build` para KTX, Final Arena, Team Fortress e TD2;
- [x] fixar e espelhar a fonte da toolchain FTEQCC usada nas compilações históricas;
- [ ] registrar imagem/ambiente de build por plataforma e hashes dos outputs;
- [ ] zerar ou justificar individualmente os 14 warnings do TD2 sem alterar gameplay;
- [ ] classificar e reduzir os 187 warnings do TF 2.9 sem modernização ampla do código;
- [ ] comparar estruturas, strings, campos, funções e comportamento dos builds com os binários oficiais;
- [ ] implementar `test-runtime` e `promote` com promoção bloqueada se a matriz falhar;
- [ ] manter o compilador id Quake Tools apenas como referência histórica: o binário atual falha em 64 bits e não deve ser usado sem correção separada.

## 3. Validação semântica dos pacotes

- [ ] criar validador de caminhos virtuais para PAK, PK3 e arquivos soltos;
- [ ] extrair referências de modelos, sons, sprites, mapas e arquivos a partir do QuakeC e gamecode disponível;
- [ ] falhar em dependência obrigatória ausente e emitir aviso explícito para dependência opcional;
- [ ] detectar colisões de maiúsculas/minúsculas em macOS, Linux e Windows;
- [ ] validar que todo overlay x86QW aponta para um upstream e uma justificativa no changelog;
- [ ] validar que arquivos reconstruídos não duplicam bytes já fornecidos por outra camada sem motivo;
- [ ] registrar mapa de dependências opcionais de TF, Final Arena, Pro-X e TD2.

## 4. Cliente ezQuake e launcher

- [x] manter ezQuake stable 3.6.9 e nightly `20260616-101233_a86996a` oficiais e coexistentes;
- [x] preservar fontes correspondentes às duas versões;
- [x] manter workaround macOS de assinatura somente para o nightly quando necessário;
- [x] executar smoke real de todos os mods em stable e nightly, não apenas testes simulados de argumentos;
- [x] corrigir no launcher o `k_defmap` do KTX antes do primeiro frame;
- [ ] propor ao ezQuake a execução direta de `PR1_LoadProgs()` quando `sv_progtype == VMI_NONE`, evitando a tentativa e o erro falsos de QVM nos mods PR1;
- [x] reproduzir as 291 linhas duplicadas no primeiro `vid_restart`, provar que somem após o primeiro encerramento normal e isolar a causa no novo registro dos mesmos objetos;
- [ ] propor correção upstream para as linhas duplicadas durante `vid_restart`, sem silenciar logs legítimos;
- [-] não distribuir cliente ezQuake modificado como stable/nightly oficial;
- [ ] se um cliente modificado se tornar necessário, criar canal explícito `x86qw-patched`, opt-in e separado;
- [-] tratar avisos OpenGL 4.6 -> 4.1, compute shader e hardware lighting no macOS como limitações informativas, salvo falha visual real;
- [ ] investigar o aviso de driver de textura GL somente se houver efeito visual reproduzível.

## 5. nQuake e conteúdo comum

- [x] preservar a sequência `nQuake -> upstream atual do mod -> harmonização x86QW`;
- [x] decompor o snapshot nQuake em componentes com recibos e inventários próprios;
- [x] preservar somente arquivos realmente consumidos;
- [x] registrar separadamente `observed_upstream_revision`, `consumed_revision` e estado do payload nQuake;
- [x] substituir o rótulo ambíguo `reference-current` por `reference-payload-current`: o master observado `721b2c9cb8f4` altera somente arquivo não consumido, enquanto o payload permanece em `e4cb23d40aa2`;
- [ ] migrar Pro-X `configs/config.cfg` sem perder dados pessoais e impedir que arquivos mutáveis sejam tratados como gerenciados;
- [x] manter `pak.lst`, aliases temporários, `-nohome` e configurações comuns compatíveis;
- [ ] validar dinamicamente a ordem completa de PK3/PAK em stable e nightly;
- [ ] manter QRP grande como addon opcional;
- [ ] revisar matchinfo como conteúdo opcional e confirmar que não entra no perfil recomendado.

## 6. Automação, CI e qualidade

- [x] manter `maintenance/manage.py` como interface única de check, update, add, verify, build, publish e commit;
- [x] validar catálogo, pacotes, inventários, integridade gerenciada, launcher e desinstalação;
- [ ] criar GitHub Actions sem bibliotecas extras para `manage.py verify`, testes do site e verificação Git LFS;
- [ ] adicionar matriz Python/macOS/Linux/Windows para lógica portável;
- [ ] adicionar smoke headless ou com logs capturados para stable e nightly onde o runner suportar;
- [ ] impedir publicação se catálogo, origem, hashes, builds ou changelogs estiverem inconsistentes;
- [ ] gerar relatório estruturado com arquivos adicionados, removidos, recompilados e não resolvidos;
- [ ] testar instalação por `curl`, update conservador, upgrade distributivo, uninstall e uninstall `--purge` em ambiente limpo;
- [ ] verificar que `update`/`upgrade` não executam quando não há mudanças e exibem plano tabulado antes da confirmação.

## 7. Upstreams e contribuição pública

- [ ] abrir PR no `QW-Group/ezquake-source` para pular a tentativa QVM quando PR1 foi selecionado explicitamente;
- [ ] abrir PR separado no ezQuake para duplicação de log em `vid_restart` somente após teste de todos os caminhos de `con_suppress`;
- [-] não abrir PR ao nQuake para `bounce2.wav`: o snapshot já fornece o arquivo no KTX; a ausência surgiu apenas da separação modular do x86QW e foi corrigida na composição local;
- [-] não abrir PR de gameplay para KTX: a correção de `k_defmap` pertence ao launcher x86QW;
- [-] Final Arena, Team Fortress, TD2 e Pro-X não possuem Git público de fonte identificado para receber estes patches;
- [ ] documentar nos changelogs a ausência de upstream Git e manter patches prontos para envio se um repositório oficial surgir;
- [ ] acompanhar CI, review e ajustes dos PRs sem misturar correções independentes.

## 8. Publicação e governança

- [ ] criar `LICENSE` do projeto e `THIRD_PARTY_NOTICES` com origem e termos de cada componente;
- [ ] decidir e documentar política pública para `id1/pak0.pak` e `id1/pak1.pak` antes do lançamento amplo;
- [ ] revisar redistribuição de todos os artefatos, inclusive nQuake, QRP, Final Arena, Pro-X, TF e TD2;
- [ ] publicar instalador e artefatos exclusivamente no repositório `x86dx2/x86qw`;
- [ ] oferecer comando de instalação por `curl` com URL estável e checksum verificável;
- [ ] redirecionar `x86.com.br/x86qw` para `x86qw.x86.com.br` com 308;
- [ ] corrigir contagens e versões exibidas no site a partir do catálogo real;
- [ ] validar a página publicada e todos os links de download após cada release;
- [ ] manter R2 fora da arquitetura até uma decisão explícita.

## 9. Servidor e infraestrutura futura

- [ ] avaliar MVDSV 1.11 como servidor dedicado depois de concluir a distribuição cliente;
- [ ] avaliar QWFWD 1.30 e QTV como roteamento/espectador modernos;
- [-] não adotar Qizmo como base moderna; manter apenas como referência histórica;
- [ ] definir catálogo, instalação, atualização e hardening do servidor em fase separada;
- [ ] não misturar a futura stack de servidor com os fixes de compatibilidade dos mods atuais.

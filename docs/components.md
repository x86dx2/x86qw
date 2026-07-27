# Componentes e atualização

O x86QW separa **conteúdo instalável** de **origem versionada**. Nem toda pasta
do nQuake corresponde a um projeto com releases: skins, miras, skyboxes, mapas e
outros recursos são coleções curadas. Nesses casos, a versão correta é o commit
imutável do `nQuake/distfiles`, não um número inventado.

Estado verificado em 27 de julho de 2026:

| Componente | Versão oferecida | Estratégia | Estado |
| --- | --- | --- | --- |
| Configuração base | `e4cb23d40aa2` | snapshot nQuake | atual no repositório de referência |
| Interface e recursos visuais | `e4cb23d40aa2` | snapshot nQuake | atual no repositório de referência |
| KTX | `1.47+nquake.e4cb23d40aa2` | release oficial sobre recursos nQuake | atualizado de `1.46-dev` para `1.47` |
| Skins de jogadores | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Miras | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Skyboxes | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Modelos | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Bandeiras do placar | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Sons | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Texturas externas | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Texturas base | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Mapas selecionados | `e4cb23d40aa2` | coleção curada nQuake | sem download externo em massa |
| Informações de partidas | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Documentação | `e4cb23d40aa2` | snapshot nQuake | atual no repositório de referência |
| QRP alta resolução | `e4cb23d40aa2` | snapshot nQuake | contém mapas 1.00 e itens 0.73 |
| Clan Arena e Pro-X | `e4cb23d40aa2` | snapshot nQuake | coleção histórica sem release atual mapeada |
| Team Fortress | `e4cb23d40aa2` | snapshot nQuake | coleção histórica sem release atual mapeada |
| Total Destruction 2 | `2.22` | pacote independente do upstream | distribuição QW completa localizada, sem mapas adicionais |

## Contrato de atualização

- `reference-snapshot`: acompanha o commit mais recente aprovado do nQuake;
- `upstream-overlay`: um artefato oficial substitui somente membros declarados
  sobre a base nQuake;
- `upstream-package`: um componente independente é validado e empacotado sem
  fingir que pertence ao snapshot do nQuake;
- todo download possui tamanho e SHA-256 antes de entrar no acervo;
- o pacote interno registra origem, membros substituídos e hashes resultantes;
- o catálogo mantém somente a versão atual, enquanto releases antigas continuam
  imutáveis no repositório de distribuição;
- `tools/check_component_updates.py` detecta novidades, mas não as publica.

O KTX é um mod de servidor. Ele permanece entre os componentes porque faz parte
do nQuake e pode ser consumido por uma instalação de servidor compatível; não é
executado nem substitui o cliente ezQuake. O x86QW ainda não distribui o runtime
MVDSV. Os únicos clientes ativos continuam sendo ezQuake stable e nightly.

O TD2 possui duas numerações públicas que não formam uma cronologia simples. A
página ativa do Arena Camper documenta a linha `2.12`, de 2023, mas o ZIP dessa
versão não está mais disponível. O pacote incorporado é a distribuição
QuakeWorld completa `2.22`, de Spinal com patch de Vegetous, ainda disponível
com `qwprogs.dat`, modelos, sons, documentação e fontes. Ele é opcional, entra
no perfil `completo` e não baixa mapas fora do acervo já aprovado do x86QW.

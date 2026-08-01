# Política de segurança

## Versões cobertas

A versão marcada como **Latest** em [GitHub Releases](https://github.com/x86dx2/x86qw/releases/latest) recebe correções de segurança. Versões históricas e snapshots nightly podem ser úteis para reprodução, mas não recebem garantia de correção retroativa.

## Reportando uma vulnerabilidade

Use o botão **Report a vulnerability** na aba [Security](https://github.com/x86dx2/x86qw/security) para abrir um advisory privado. Não publique uma issue, discussão, pull request ou prova de conceito antes da coordenação da correção.

Inclua, quando possível:

- componente e versão afetados;
- sistema operacional e arquitetura;
- pré-condições e impacto observado;
- passos mínimos para reproduzir;
- logs sanitizados e uma sugestão de mitigação;
- informação sobre divulgação ou prazo já combinado com terceiros.

## Escopo prioritário

- validação de pacotes, hashes e catálogos;
- travessia de caminho, links e escrita fora do destino;
- execução de comandos ou argumentos não confiáveis;
- vazamento de senhas, tokens ou configuração privada;
- locks, journals e recuperação que possam atingir processos ou arquivos alheios;
- exposição de MVDSV, QTV ou QWFWD além do `--bind` solicitado;
- workflows, publicação e cadeia de suprimentos.

Falhas exclusivamente no upstream também podem afetar o x86QW. Envie o relato aqui se a distribuição, configuração ou empacotamento ampliar o impacto; a coordenação com o upstream será feita sem expor o pesquisador.

## Processo esperado

O projeto acusará recebimento pelo advisory, validará o escopo, preparará a correção e combinará a divulgação. Não há recompensa financeira prometida. Relatos de boa-fé, que evitem indisponibilidade, acesso a dados de terceiros e exposição prematura, são bem-vindos.

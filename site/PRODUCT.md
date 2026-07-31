# Product

## Register

brand

## Users

Pessoas descobrindo QuakeWorld e jogadores veteranos que querem uma forma atual,
confiável e multiplataforma de instalar, atualizar e preservar o jogo. Quem está
chegando precisa entender rapidamente o que é o projeto e como começar; quem já
joga precisa reconhecer transparência técnica, controle e respeito ao ecossistema.

## Product Purpose

Divulgar o QuakeWorld e apresentar o x86QW como uma distribuição moderna,
reproduzível e auditável. O site deve transformar curiosidade em confiança:
explicar a proposta, tornar o estado real da distribuição visível e conduzir o
visitante à instalação sem esconder proveniência, suporte efetivo ou limitações.

## Brand Personality

Direta, competitiva e confiável. A voz é segura e objetiva, com a energia de uma
partida veloz e a precisão de uma ferramenta bem mantida. O resultado deve parecer
contemporâneo sem apagar a história do QuakeWorld.

## Anti-references

Não parecer nostalgia pixelada excessiva, estética gamer neon/cyberpunk ou uma
landing page SaaS genérica. Evitar também futurismo ornamental, jargão promocional,
interfaces cheias de cartões iguais e qualquer tentativa de esconder limitações
de plataforma ou validação.

## Fatos públicos

Versão, contagens, comandos, jogos, runtimes e plataformas vêm da projeção
gerada `public/api/v1/product.json`. A fonte é o conjunto canônico em
`maintenance/inventory/`, `dist/installer/VERSION` e o catálogo de pacotes.
Textos do site não podem manter números divergentes: os testes comparam a página
com essa projeção e a validação integral rejeita um JSON desatualizado.

O produto atual oferece cinco jogos, KTX com modos e Frogbots, hospedagem por
MVDSV, relay QTV e proxy QWFWD. O cliente macOS é universal; os três serviços
no macOS são arm64. Linux amd64 e Windows x64 possuem cliente e serviços.

## Design Principles

- Começar pelo jogo: comunicar velocidade, comunidade e longevidade antes da infraestrutura.
- Provar confiança: transformar versões, hashes, licenças e estado do catálogo em evidência legível.
- Servir dois níveis de experiência: oferecer uma entrada clara a novatos sem simplificar demais para veteranos.
- Ser honesto sobre o estágio: distinguir claramente o que já funciona, o que está em auditoria e o que virá depois.
- Modernizar sem descaracterizar: usar linguagem visual atual sem imitar interfaces retrô ou tendências gamer passageiras.

## Accessibility & Inclusion

Atender WCAG 2.2 AA. Garantir navegação completa por teclado, foco visível,
contraste mínimo de 4.5:1 para texto comum, estrutura semântica, textos alternativos
e informação nunca transmitida apenas por cor. Respeitar `prefers-reduced-motion`
e manter o conteúdo integral quando animações forem desativadas.

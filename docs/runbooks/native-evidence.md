# Referência — evidência nativa

Este documento define a fronteira de verdade para uma futura evidência nativa.
O baseline 0.7.3 não contém workflow ou ferramenta de `native-smoke`,
`signed-evidence`, `native-release` ou `release-evidence`; portanto não há
passo operacional nem evidência multiplataforma a registrar neste checkout.

## Fluxo atual

Validações locais do baseline podem ser executadas no Mac com os comandos de
verificação documentados no runbook de release, mas isso não cria
`release-evidence.json`. A presença de `Linux-X64`, `Windows-X64`,
`macOS-ARM64` e `macOS-X64` em contratos/catalogos preserva compatibilidade e
não afirma que essas plataformas foram executadas ou validadas neste snapshot.

## Compatibilidade preservada

Quando uma ferramenta ou schema de evidência for adicionado por uma decisão
explícita, um `release-evidence.json` fornecido manualmente deverá passar por
validação estrutural e criptográfica correspondente. O arquivo não pode ser
criado implicitamente nem transformar um candidato Mac em evidência
multiplataforma.

A cobertura histórica reconhecida pelos validadores permanece exatamente
`Linux-X64`, `macOS-ARM64` e `Windows-X64`, com `macOS-X64` condicional. Esses
nomes não são uma definição de pronto da release atual.

## Limites de verdade

- teste portável no Mac não é smoke nativo de Linux, Windows ou macOS Intel;
- fixtures e relatórios legados não são apresentados como evidência atual;
- trust de catálogo (`root`, `current`, `snapshot`) continua separado da
  compatibilidade opcional de evidência;
- nenhum resultado ausente pode ser preenchido por fixture, relatório legado
  ou inferência de catálogo.

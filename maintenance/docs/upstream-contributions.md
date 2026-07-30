# Contribuições aos upstreams

O x86QW preserva upstreams sem misturar correções independentes. Um PR só é enviado quando existe Git público oficial, a mudança é genérica para o upstream e pode ser demonstrada fora da distribuição x86QW.

| Projeto | Git público | Mudança x86QW | Destino |
| --- | --- | --- | --- |
| ezQuake | `QW-Group/ezquake-source` | pular a tentativa QVM quando `sv_progtype` seleciona PR1 explicitamente | [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149), draft, aguardando CI/review |
| ezQuake | `QW-Group/ezquake-source` | tornar idempotente o registro do mesmo objeto/função durante `vid_restart` | [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150), draft, aguardando CI/review |
| KTX | `QW-Group/ktx` | definição antecipada de `k_defmap` | não enviar; a correção pertence ao launcher x86QW |
| nQuake | `nQuake/distfiles` | `bounce2.wav` ausente após modularização | não enviar; o arquivo já existe no KTX do snapshot e a correção pertence à composição x86QW |
| Final Arena | não localizado | patches futuros de fonte | manter no x86QW até surgir upstream oficial |
| Pro-X | não existe fonte pública | ENT de compatibilidade | não enviar como código; o autor reteve a fonte deliberadamente |
| Team Fortress 2.9 | não localizado | remoção de binds remotos forçados | manter patch pronto e documentado |
| TD2 2.22 | não localizado | correções de compatibilidade QuakeC | manter patch pronto e documentado |

## Regras de envio

- criar um branch por problema, a partir do branch padrão atual do upstream;
- não incluir branding, configuração ou caminhos x86QW em correção genérica;
- descrever versão, ambiente, passos de reprodução, comportamento anterior e posterior;
- anexar teste automatizado quando a base permitir; caso contrário, registrar matriz manual;
- não combinar limpeza, formatação e mudança funcional;
- referenciar o changelog do mod no x86QW como histórico, não como justificativa exclusiva;
- acompanhar CI e review até merge ou decisão explícita do mantenedor.

## Estado em 30 de julho de 2026

- os dois PRs partem do `master` atual `a86996a3` e não contêm branding,
  configuração ou arquivos x86QW;
- ambos passaram por inspeção de diff e `git diff --check`;
- o host de reprodução não possui CMake, portanto a compilação será validada
  pelo CI do upstream; o GitHub ainda não havia iniciado checks no momento do
  envio;
- os demais mods não receberam PR porque não foi localizado um Git público que
  represente exatamente a versão distribuída. `QW-Group/Quake-Custom-Team-Fortress`
  é Custom TF, não o Team Fortress 2.9 preservado aqui.

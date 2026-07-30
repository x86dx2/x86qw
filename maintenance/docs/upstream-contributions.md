# Contribuições aos upstreams

O x86QW preserva upstreams sem misturar correções independentes. Um PR só é enviado quando existe Git público oficial, a mudança é genérica para o upstream e pode ser demonstrada fora da distribuição x86QW.

| Projeto | Git público | Mudança x86QW | Destino |
| --- | --- | --- | --- |
| ezQuake | `QW-Group/ezquake-source` | pular a tentativa QVM quando `sv_progtype` seleciona PR1 explicitamente | PR separado com reprodução em mod PR1 |
| ezQuake | `QW-Group/ezquake-source` | duplicação de log durante `vid_restart` | PR separado somente após validar todos os usos de `con_suppress` |
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

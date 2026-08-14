# Operação TUF — renovação e recuperação

O monitor público é somente observabilidade. Ele autentica a cadeia e abre ou
atualiza uma issue quando timestamp, snapshot ou targets entram na janela de
alerta; ele não assina, renova nem publica metadata.

## Drill obrigatório antes de 1.0

O custodiante deve executar o drill em uma máquina de operação isolada, com as
chaves privadas fora do checkout e com um repositório TUF assinado localmente:

```sh
python3 maintenance/tools/tuf_operation_drill.py \
  --key-dir /secure/x86qw/tuf-keys \
  --root maintenance/trust/root.json \
  --catalog /secure/x86qw/candidate/catalog.json \
  --repository /secure/x86qw/tuf-current \
  --output /secure/x86qw/records/tuf-drill-YYYYMMDD.json \
  --operator release-operator \
  --custody-host offline-signer-01 \
  --sla-hours 6
```

O comando:

1. autentica o repositório corrente e confere o target do catálogo;
2. gera uma renovação em diretório temporário com versão seguinte;
3. autentica a renovação contra a mesma root incorporada;
4. comprova que target e root não mudaram;
5. injeta uma expiração somente na cópia temporária e exige falha;
6. comprova a recuperação usando a renovação saudável;
7. grava um relatório novo sem overwrite e marca `published: false`.

O drill não é autorização de publicação e não deve receber uma chave root
online. Root e targets permanecem offline. Se for adotado um signer online,
ele deve possuir somente a autoridade de timestamp, host isolado, auditoria,
rotação, kill switch e alertas; a política criptográfica não muda.

Para registrar o exercício no gate final, o relatório aprovado deve ser enviado
ao workflow protegido
`.github/workflows/tuf-operation-drill.yml`. Esse workflow vincula o relatório
ao `candidate.json`, verifica target/root/expiração/recuperação e publica um
artifact imutável somente com o JSON do relatório. A promoção de `1.0.0` exige
as coordenadas desse artifact e o digest do relatório; um relatório histórico
sem `operation` não é aceito.

Após o upload, o workflow grava no resumo do run `operation_artifact_id`,
`operation_artifact_digest` e `operation_artifact_name`. Esses valores devem ser
copiados para os inputs correspondentes da promoção final; não se deve inferir
o ID do artifact apenas pelo nome.

## Evidência mínima

Anexar ao issue de soak e ao gate final:

- recibo JSON do drill;
- custodiante/operador, host de custódia e SLA de timestamp registrados no
  campo `operation`;
- versão anterior e renovada de cada role;
- digest e tamanho do target antes/depois;
- confirmação da root inalterada;
- falha de expiração simulada;
- recuperação verde;
- responsável, data UTC e host de custódia;
- confirmação de que nenhuma metadata foi publicada pelo drill.

Ausência de um relatório não é equivalente a drill executado. Até a evidência
ser anexada e revisada, a promoção de `1.0.0` permanece `NO-GO`.

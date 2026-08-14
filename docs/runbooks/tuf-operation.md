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

## Handoff de renovação somente de timestamp

Quando a operação adotar um signer limitado, a renovação deve usar uma única
chave cujo `keyid` pertença à role `timestamp`. A ferramenta não carrega chaves
de root ou targets, recusa uma chave de outra role e autentica o repositório
resultante antes de produzir o relatório:

```sh
python3 maintenance/tools/tuf_timestamp_renewal.py \
  --repository /secure/x86qw/tuf-current \
  --root maintenance/trust/root.json \
  --catalog /secure/x86qw/catalog.json \
  --timestamp-key /secure/x86qw/timestamp-key.pem \
  --key-id <timestamp-key-id> \
  --output /secure/x86qw/tuf-renewed \
  --report /secure/x86qw/records/tuf-timestamp-renewal-YYYYMMDD.json \
  --lease-hours 24
```

O diretório de saída deve diferir do repositório corrente e não pode existir.
O relatório marca `published: false`, registra a role/key id, a versão anterior
e a nova, e falha se qualquer byte além de `metadata/timestamp.json` mudar.
Esse handoff ainda precisa passar pela aprovação protegida e pelo publicador;
executar a ferramenta não atualiza o endpoint público.

Novos relatórios usam `format: 2` e devem conter `role_versions` para as três
roles (`timestamp`, `snapshot` e `targets`), com `current` e `renewed` em cada
uma. O verificador rejeita o formato histórico sem esse vínculo individual;
isso evita que um `max` agregado esconda uma role que não avançou.

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

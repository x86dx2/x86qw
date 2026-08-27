# Errata — versão da root TUF pública

Documentos de auditoria de 15 e 16 de agosto de 2026 registraram `root v2`.
Essa afirmação não corresponde aos bytes preservados: o handoff, a renovação e
a publicação referenciados nesses documentos contêm somente `1.root.json`, cuja
versão assinada é `1` e cujo SHA-256 é
`660af63e52a033290adf8899d2078a779c04e04cf5d1fac465b4aa2e04937201`.

Não existe `2.root.json` nos artefatos citados. A autenticação do catálogo
permanece válida sob root v1; a errata corrige apenas a versão narrada. Os
snapshots históricos não são reescritos. A projeção corrente declara root v1 e
a montagem do site deriva esse número da cadeia de roots recebida no handoff,
inclusive quando uma rotação futura publicar uma sequência contígua.

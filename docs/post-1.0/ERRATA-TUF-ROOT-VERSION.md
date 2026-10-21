# Erratum — version of the public TUF root

Audit documents from August 15 and 16, 2026 recorded `root v2`.
That claim does not match the preserved bytes: the handoff, the renewal and the
publication those documents reference hold only `1.root.json`, whose signed
version is `1` and whose SHA-256 is
`660af63e52a033290adf8899d2078a779c04e04cf5d1fac465b4aa2e04937201`.

There is no `2.root.json` in the cited artifacts. Catalog authentication stays
valid under root v1; the erratum corrects only the stated version. The
historical snapshots are not rewritten. The current projection declares root v1
and the site build derives that number from the chain of roots received in the
handoff, including when a future rotation publishes a contiguous sequence.

# Trust fixtures

`root.json`, `snapshot.json`, `current.json` and the rotation/threshold
variants contain public keys and signatures generated independently with
OpenSSL 3 using:

```text
openssl dgst -sha256 -sign <offline-key.pem> \
  -sigopt rsa_padding_mode:pss \
  -sigopt rsa_pss_saltlen:32 \
  -sigopt rsa_mgf1_md:sha256
```

Private keys are intentionally not stored in this repository. The catalog
fixture is the unchanged public `site/public/api/v1/catalog.json`; its bytes
are read by the tests rather than copied.

`trusted-state-v1.json` exercises the legacy empty checkpoint and
`trusted-state-v2-evidence.json` exercises an evidence-only checkpoint that can
be upgraded atomically. `trusted-state-v2-incomplete.json` is intentionally
invalid: it contains a catalog role without an authenticated root and must
always be rejected.

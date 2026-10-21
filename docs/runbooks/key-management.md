# Key runbook

The public TUF repository uses Ed25519 keys and four roles, defined by the
root in `maintenance/trust/root.json` (also embedded in the CLI):

| Role | Threshold | Where the key lives | Valid for |
|---|---|---|---|
| root | 2 of 3 | offline, on the maintainer's Mac | until the next root (v1 expires 2027-08-10) |
| targets | 2 of 3 | offline, on the maintainer's Mac | 90 days from signing |
| snapshot | 1 of 2 | offline, on the maintainer's Mac | 90 days from signing |
| timestamp | 1 of 2 | offline and in the `TUF_TIMESTAMP_KEY_B64` secret | 30 days, renewed every week |

- The offline keys live in `.tuf-production/keys`, ignored by Git. They never
  go into GitHub, GitLab, CI, the catalog or logs.
- Only the timestamp role's key is online; it does not sign root, targets or
  snapshot, so it cannot change the catalog.
- `x86qw-release sign` signs a new generation with the offline keys; the
  weekly renewal uses `x86qw-release renew-timestamp` with the online key. The
  step by step is in the [release runbook](release.md).
- A new root needs the signature of the current root (threshold 2) and of the
  new one. The CLI follows the chain of published roots from the one it
  embeds.

Do not use hashes provided by the catalog itself as authentication. Do not
generate production keys in tests.

# Runbook — TUF key compromise

Complements [ADR 0006](../adr/0006-tuf-trust-metadata.md) and the
[key runbook](key-management.md). Nothing here runs automatically.

## Online timestamp key (`TUF_TIMESTAMP_KEY_B64`)

Whoever holds this key can extend the validity of the published generation,
but cannot change the catalog or the packages.

1. Disable the `tuf-timestamp-auto-renewal.yml` workflow and delete the
   secret.
2. Prepare a new root that replaces the timestamp role's key (see below).
3. With the offline keys, sign a new generation (`x86qw-release sign`) and
   publish it through the normal PR and deploy flow.
4. Store the new online key in the secret and re-enable the workflow.

## Offline keys (targets, snapshot or root)

1. Stop publishing: no merge that touches `site/public` until the end.
2. Generate the new keys outside the checkout, on a trusted machine.
3. Prepare root `N+1` without the compromised keys. It needs the signature of
   the current root (threshold 2 of 3) and of the new one; if the current root
   cannot reach the threshold, stop: there is no recovery without reinstalling
   the CLI with a new embedded root.
4. Publish the new root in `site/public/api/v1/trust/metadata/N+1.root.json`
   and in `maintenance/trust/root.json`, sign a new generation with the new
   keys and check with `x86qw-release monitor --checkout` after the deploy.
5. Record what happened in an issue, without secrets.

`x86qw-release` has no command yet to generate a new root; that step is
manual with the go-tuf library. Private keys never go into a shell, argv,
journal, ticket or log.

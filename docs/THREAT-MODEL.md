# Threat model, in brief

## Assets

- the published catalog, packages and TUF metadata;
- the TUF keys (root, targets, snapshot and timestamp);
- personal settings and service passwords;
- the user's state, receipts, logs, demos and PAKs;
- the CLI and the installed native executables.

## Adversaries

- a compromised mirror or a malicious remote response;
- a malformed ZIP or PK3;
- a concurrent process trying to swap a file during validation;
- an unprivileged user or an unrelated local process;
- old metadata replayed (rollback or freeze);
- an abrupt failure during a change or while a service stops.

## Controls

Downloads require HTTPS, limits, a deadline, size and SHA-256; files are
downloaded into private staging and promoted atomically. The file boundary
walks the whole ZIP before extracting and rejects traversal, links, special
types, collisions and expansion limits. Sensitive Windows objects use a
protected DACL.

Sessions have a per-installation lock, a controller identity and a journal;
recovery is conservative and never stops a PID without matching token and
executable. Passwords never go into argv, logs, journals or messages.

The catalog is only accepted authenticated by TUF. The CLI embeds the root
(v1), with Ed25519 keys and four roles: root and targets with a threshold of 2
of 3, snapshot and timestamp with a threshold of 1 of 2. It verifies
signatures, thresholds, expiry and monotonic versions, keeps the trusted
metadata in the cache and rejects rollback and a timestamp that reuses a
version with other bytes. Targets authorizes a single target, the catalog, by
size and SHA-256; the bootstrap pins the CLI by size and SHA-256, and so does
each catalog package.

The bootstraps are served over HTTPS. The Unix one only runs its body on the
last line, so a truncated download runs nothing; the Windows one checks the
binary's size and SHA-256 before running it.

## Limits

- The offline keys live on a single Mac of the maintainer; the model does not
  cover the loss or compromise of that machine beyond the
  [compromise runbook](runbooks/trust-compromise.md).
- The online timestamp key lives in a GitHub secret; whoever gets it can
  extend the validity of the published generation, but cannot change the
  catalog.
- The model does not cover licensing or redistribution of the PAKs.
- Native validation that was not run on a platform cannot be inferred from CI
  tests; the public claim must be reduced or stay conditional.

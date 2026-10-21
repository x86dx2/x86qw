# ADR 0006 — Trust metadata for the catalog and releases

- **Status:** implemented locally; not published
- **Date:** 2026-08-04
- **Issue:** #48
- **Scope:** authenticate the catalog and the public metadata without changing
  the existing catalogs/releases of the 0.7.x series

## Context

The `sha256` fields that appear inside `site/public/api/v1/catalog.json`
describe the artifacts, but they are data provided by the catalog itself. A
mirror able to swap the catalog can also swap those hashes. They are not
authentication. The client needs a pinned root, monotonic versions, expiration
and a signed chain that is independent of the mirror.

The installed runtime must stay standard-library only. Python 3.10 offers no
Ed25519, RSA-PSS or other asymmetric verification in the `hashlib` module.
Adding a cryptographic dependency in this slice would change the packaging and
would require a separate decision. The local implementation therefore contains
only a minimal RSA-PSS-SHA256 verifier (RFC 8017), with no private key
operation. It requires an independent cryptographic review and cross vectors
with OpenSSL before it can be considered fit for a production key.

## Decision

`x86qw_runtime/trust.py` is the canonical boundary. It receives already bounded
bytes, the UTC clock and the `TrustedVersions` persisted by the caller. It does
no networking, filesystem access, state updates, `maintenance` imports or
writes. The caller must persist the result only after full validation.

The format is TUF-like, with an exact envelope:

```json
{"signatures":[{"keyid":"…","sig":"base64url without padding"}],"signed":{}}
```

Only the `signed` object is signed, in canonical UTF-8 JSON (`sort_keys=True`,
compact separators, NFC, no floats, duplicate keys or non-finite values).
`keyid` is the SHA-256 of the canonical public object. The RSA key uses
`keyval.public.n` in hexadecimal and `keyval.public.e` as an integer; the
modulus must be at least 3072 bits. PSS uses SHA-256, MGF1-SHA256 and a fixed
32-byte salt, comparing the digest with `hmac.compare_digest`.

`release-evidence.json` follows the same principle: the canonical form removes
the `signatures` field before verification and accepts a closed list of
distinct signers. Old evidence with the singular `signature` field is accepted
only when the `evidence` role has threshold `1`; it does not lower a higher
threshold. The initial root must contain, and also present a valid signature
from, the pinned `ROOT_KEY_ID` — declaring that key in the role without its
signature is not a sufficient anchor.

The roles are separate:

| Role | Content | Main protection |
| --- | --- | --- |
| `root` | keys and thresholds | offline root, contiguous rotation and double signature |
| `current` | short pointer to a snapshot | anti-freeze and monotonic version |
| `snapshot` | digest/size/version of the catalog | anti-rollback and catalog binding |
| `evidence` | reserved for `release-evidence.json` | never reuse the `current` key |

The initial root is `maintenance/inventory/trust/root.json`; the matching public
key is pinned in `ROOT_PUBLIC_KEY`/`ROOT_KEY_ID` in the CLI/runtime. The
canonical artifacts are:

- `maintenance/inventory/trust/current.json`;
- `maintenance/inventory/trust/snapshot.json`;
- `site/public/api/v1/catalog.json` (only referenced, unchanged).

`current` expires in at most seven days and points exactly to the size, digest
and version of `snapshot.json`. `snapshot` points exactly to the size, digest
and version of the public catalog. URLs, absolute paths, backtracking,
backslashes and extra fields are rejected. So the hashes stay important for
binding and corruption detection, but are accepted only after the signature of
the role that contains them.

### Integration in the installed client

The installed client keeps compatibility with old releases: with no local
metadata, no configured trust URLs and no persisted state, the legacy catalog
flow stays available. When the local package contains the complete set
`maintenance/inventory/trust/{root,current,snapshot}.json`, it is verified
before the catalog is used. For a remote catalog, the same protection is turned
on by `X86_QW_TRUST_METADATA_URL` (a base holding the three files) or by the
explicit URLs `X86_QW_TRUST_{ROOT,CURRENT,SNAPSHOT}_URL`.

`X86_QW_TRUST_METADATA_REQUIRED=1` (also accepted as
`X86_QW_REQUIRE_TRUST_METADATA=1`) turns the absence of a metadata source into
an error; a signature, expiration, rollback, rotation or digest failure always
fails closed when a source is found. Remote root/current/snapshot are
downloaded and validated before the catalog request, and the catalog is
accepted only after the final check of the signed digest and size.

After a successful verification, the client atomically writes
`.x86qw/trust/versions.json` with a private mode. Format `2` holds the
versions, the digests, the previous root's public envelope and, when there is
promoted evidence, the `evidence_version`/`evidence_digest` pair. No private
key or secret is persisted. The reader explicitly imports legacy format `1`
(without evidence state) and always emits format `2`; extra fields or an
implicit mix of the two contracts fail closed. If that state already exists,
the absence of updated metadata does not allow silently going back to the
unauthenticated flow.

Before pointing these URLs at production, the release team must decide, as
humans, the official endpoint/mirror, review the RSA-PSS implementation and
approve the offline key. This local integration does not change
`site/public/api/v1/catalog.json`, the public bootstraps or
`dist/installer/VERSION`, and is not an authorization to publish.

### Official trust gate

The official endpoint, the set of mirrors and the production public key do not
exist yet in this stabilization checkout. `ROOT_PUBLIC_KEY` and the files in
`maintenance/inventory/trust/` are public fixtures for local vectors; they must
not be promoted, swapped for an invented key or treated as the operational
anchor of the next release. The verifier receives bytes already obtained and
does no endpoint discovery, automatic rotation, publication or private key
custody.

Enabling official trust is an external gate: the release authority must choose
an immutable HTTPS endpoint, hold the offline key, define the signers/thresholds
of `root`, `current`, `snapshot` and `evidence`, run the independent
cryptographic review and record the ceremony. Only after that approval may the
installed caller point the trust URLs at production and replace the fixture
anchor in an explicit change. Until then, legacy compatibility stays limited to
the public 0.7.3 bundles without a complete local set of metadata; if any part
of the set is present, the local flow fails closed, and persisted state
prevents silently going back to the unauthenticated catalog.

A version lower than the persisted one is a rollback. The same version without
the same digest is equivocation and also fails. Rotating root `N -> N+1`
requires the old root's threshold and the new one's; skipping a version,
removing a key without that transition or using a revoked role fails closed.
The evidence applies the same rule to SemVer precedence: a lower version is a
rollback and the same version with a different canonical digest is
equivocation. Its expected identity is mandatory and must match the top-level
`version`/`commit`, `candidate.version`, `candidate.commit` and the manifest's
SHA-256. The version state and the previous trusted root belong to the caller
and must be written atomically. `verify_release_evidence` returns the document
as a compatible mapping and, with it, the `TrustedVersions` already updated
with the root (version, digest and public envelope) and with
`evidence_version`/`evidence_digest`; that keeps the rotation chain even when
the persisted state started as evidence-only. The caller must persist that
checkpoint, not rebuild it from the previous version.

## Compromise/rotation runbook

1. Stop promotion and publication; do not replace the existing catalog,
   snapshot or pointer.
2. Keep the received bytes, logs and digests as evidence. Do not record the
   private key, password or seed material in argv, a journal or a ticket.
3. Revoke the compromised role in the new `root` (`N+1`), generate a key
   outside the workspace and get the old and new root thresholds. If the old
   root is unavailable, treat it as a manual trust recovery and not as an
   automatic update.
4. Publish the new immutable assets first; then `snapshot`; `current` last.
   Never publish a pointer that comes before the assets.
5. Invalidate caches/mirrors holding the compromised version and check size and
   digest on every mirror.
6. Run the chain validation in a clean environment and record the independent
   cryptographic review, the OpenSSL vectors and the human approval before
   reopening promotion.

Production private keys stay offline and are not provided by the runtime. The
local files and fixtures of this PR use an offline test key to allow
reproducible verification; that is not evidence of a production publishing
key.

## Consequences and limits

- The chain authenticates content and metadata without trusting hashes
  provided by the catalog itself.
- The local clock is still a dependency for expiration/anti-freeze; a host with
  a compromised clock needs an external time policy.
- The RSA-PSS implementation is a cryptographic surface of its own. Before
  version 1.0, there must be an independent review and cross vectors. If the
  gate is not approved, replace the boundary with a maintained library (for
  example, Python-TUF/cryptography) in an explicit dependency change.
- Persistence of `TrustedVersions`, the downloader, asset publication and
  `release-evidence.json` belong to the corresponding PRs/owners; this ADR does
  not simulate those paths nor authorize publication.

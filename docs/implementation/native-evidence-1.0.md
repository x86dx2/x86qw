# Native evidence of the 1.0 line (historical)

> This document describes the Python harness that produced the M3 evidence of the
> 1.0.x releases. It was removed along with Python (#328) and does not apply to the
> 2.x CLI; it stays here as a record.

The executor `maintenance/tools/native_macos_harness.py` is the only path that can
produce an approved `macOS-ARM64` handoff. It requires a Mac M3 (macOS arm64), Apple M3,
a standard user, a verified candidate and a closed plan bound to the manifest's SHA.
The handoff keeps a closed hardware attestation (`chip` and `model`); a
report that only declares `macOS`/`arm64`, without confirming Apple M3, is rejected.
Each case uses `subprocess.Popen(..., shell=False)`, records the exit code and only
passes when each assertion has a file produced after the process. Each
case also needs exactly one `case-attestation` artifact holding the
transient nonce received by the process; that keeps a static file
of the candidate from being counted as an observation of that run. Cases
run in isolated POSIX groups and timeouts terminate the descendants.
In the QWFWD case, the candidate config is copied and its `net_port` is overwritten
only in the scratch with an ephemeral port; the original file and its digest are not
changed, avoiding a collision with any personal service on `30000`.
After the connection `j`, the case sends and waits for a `print` reply forwarded
by the server before the client's first payload; that removes the handshake
race that could drop the first datagram and produce a false negative.

## Mandatory cases

The plan must cover exactly:

- a clean installation and an installation over an existing tree, at a path with a space and
  Unicode;
- a real migration of a `0.7.13` installation, preserving personal settings and
  PAKs;
- stable and nightly with a window, a loaded map and shutdown;
- KTX without bots and KTX Duel with a real Frogbot, a fixed skill and the identity
  `x86QW`, plus Final Arena, Pro-X, Team Fortress and TD2 with gamecode and map;
- MVDSV with a valid MVD;
- QTV with a readable HTTP/upstream/stream;
- QWFWD with real UDP forwarding;
- update/upgrade in dry-run for the plan, and real update/upgrade apply;
- verify, repair in dry-run, repair with a permission corruption restored,
  migrate apply, cleanup, conservative uninstall and explicit purge.

An exit code of zero alone is not enough. The plan must bind each assertion to
an observable artifact; the handoff is then validated by
`native_release_smoke.py`, turned into a record by
`native_release_evidence.py` and consumed by the release gates. The canonical
body binds the candidate's manifest by SHA-256; the provenance of the run and the
artifact is validated by `verify_external_handoff.py`. The
`release_evidence_binding.py` stays available for private audits that
also carry the handoff's raw files, but it is not claimed as part
of the current redacted artifact.

## Honest limits

- portable CI is not a native smoke;
- Linux-X64, Windows-X64 and macOS-X64 stay `preview` in this cycle;
- stable macOS stays `conditional` while Developer ID signing and
  notarization are not proven;
- a `not-run`, synthetic, `fixture`, `mock` or `dry-run` report is never
  turned into release evidence;
- the harness records no serial, token, cookie, password or custody path.

The M3 plan is a protected input of the runner and is not invented by the workflow.
If it is missing or points to another candidate, promotion stops before
approval and no asset or metadata is published.

The `.github/workflows/native-m3.yml` workflow receives the `candidate_run_id`, the
`candidate_artifact_id`, the exact name
`candidate-<commit>-<run_id>-<run_attempt>`, the commit and the SHA-256 of
`candidate.json`. Before the download, it queries the GitHub API and confirms
that name, ID, run, workflow and commit are the same publication; then it downloads the
immutable artifact of the `build-once` run, checks the SHA of `candidate.json`, generates the plan with the
contract inside the candidate and runs that same harness. It does not use
`quake-world/`, does not install the maintainer's personal tree and does not sign
evidence. The artifact produced is deliberately `pending`,
`signed: false` and `promotable: false`; the protected signing ceremony is
a later, mandatory step. The `native-m3-input` upload holds only the
redacted aggregate, the plan, the unsigned platform record and the canonical JSON
body that will be signed; stdout, stderr, raw receipts and the temporary runtime
stay in the runner's private scratch.

The `.github/workflows/sign-native-evidence.yml` workflow is the protected adapter
for custody. Under ADR 0007's waiver, the single maintainer can perform the
signing outside CI; that does not create independent human review. The workflow
checks through the API that the `build-once` artifact and the
`native-m3` run belong to the repository, the commit and the given IDs/names,
downloads the same bytes, receives only a
public envelope of signatures (`signatures.json` in base64) and requires
`M3_TRUST_ROOT_B64` in the protected environment. `maintenance.tools.assemble_release_evidence`
refuses any private key, requires `--trust-root`, checks the hash of the canonical
body and authenticates the aggregate before the workflow produces
`native-m3-signed`. That workflow does not sign: the signature is generated by the
authorized maintainer outside CI and CI only verifies the public envelope.
The versioned public root is `maintenance/trust/m3-root.json`; the cryptographic
policy is still 2-of-3 and each piece of evidence needs two distinct keys.
The three private seeds live in the Proton Pass vault `x86QW`; the single maintainer
is the custodian of all of them, with no claim of human independence.

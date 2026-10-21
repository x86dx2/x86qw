# Native macOS M3 harness

## State

The harness and the aggregate of this step are local infrastructure. The real
run of the candidate is still an operation outside the portable CI; at this
checkpoint, a local M3 run was completed and produced a v1 handoff and a v2
smoke report. No evidence was signed and no support or release
state was promoted.

## Executable input

Each candidate produced by step F binds the runtime bytes in
`runtime/{clients,servers,services}` and holds the candidate-owned contract and Python
entrypoint. `candidate.json` is the authority for the version, commit and digest;
that makes it possible to generate a deterministic local plan, but it does not replace
running on Apple M3 hardware nor create release evidence by itself.

A candidate able to run the smoke must declare, in the `artifacts` of
`candidate.json`, both the contract and the executable. The closed contract is:

```json
{
  "format": 1,
  "project": "x86qw",
  "platform": "macOS-ARM64",
  "protocol": "x86qw-native-case-v1",
  "entrypoint_artifact": "runtime/native-smoke/macos-arm64/x86qw-native-smoke"
}
```

With that explicit capability, the adapter generates the plan outside the candidate:

```sh
python3 -m maintenance.tools.native_plan_adapter \
  --candidate /path/candidate \
  --expected-candidate-sha256 CANDIDATE_JSON_SHA256 \
  --entrypoint-contract runtime/native-smoke/macos-arm64/entrypoint.json \
  --output /outside/the/candidate/native-plan.json
```

The format 2 plan is deterministic and closed. It:

- binds `version`, `commit` and the SHA-256 of the `candidate.json` bytes;
- records exactly the 18 canonical cases, in the contract's order;
- binds the contract and entrypoint to the declared artifacts, by size and SHA-256;
- uses only the literal protocol `--candidate-root {candidate} --case NAME
  --scratch-root {scratch} --receipt {receipt}`;
- embeds no absolute paths, timestamps or inferred commands.

The two installation cases still use that same closed protocol, but
the candidate-owned entrypoint derives the stable macOS version from the candidate itself
and passes the installer the explicit selections of platform, channel, release and
absence of components. The CLI keeps the menus when those options are not
given; in the harness, `stdin` stays closed (`DEVNULL`) and no human
prompt is reopened. The `output-dir` is also normalized to an absolute path before
creating scratch, logs and receipts, avoiding duplicated paths when the caller
uses a relative path.

The first installation case also uses `--online-only`, extracts the two
launchers from the candidate bundle next to `x86qw.pyz` and runs, in the temporary
installation created by the case itself, `help`, `version`, `changes` and
`migrate --dry-run`. The native receipt only passes when the four commands
end with exit code zero, the help lists `changes` and `migrate`, and the
launcher's version matches the candidate's version. That proves the installed CLI without
reusing a personal installation or depending on the `quake-world` tree.

A missing contract results in exit code 2 and `not-run`, with no plan. A malformed
contract, an unregistered artifact or diverging bytes result in an error,
also with no plan. The adapter never changes the candidate.

## Execution and aggregate

With the exact candidate and plan available on a Darwin/arm64 host whose chip was
explicitly observed as Apple M3 (M3, M3 Pro, M3 Max or M3 Ultra):

```sh
python3 -m maintenance.tools.native_macos_harness run \
  --candidate /path/candidate \
  --plan /path/native-plan.json \
  --output-dir /path/local-handoff
```

Before running, the harness copies the exact bytes of the entrypoint into a
private directory under `--output-dir`, validates size and SHA-256 and makes only
that copy executable. The original artifact stays unchanged. The entrypoint
runs with the explicit Python interpreter, without depending on a shebang or a
shell. Each case uses its own scratch for extractions, but shares the installation's
state; the preconditions `clean → installed → uninstalled` are
recorded in the candidate-owned receipt. The receipt holds the client,
server, service or installer artifact actually selected, its size, SHA-256 and
the run's result. Candidate, contract, entrypoint and artifacts are
revalidated before and after each case.

The local detector uses `/usr/sbin/system_profiler SPHardwareDataType -json`,
with a timeout, and keeps only chip and model. A detection failure, Apple M1/M2/M4,
a different architecture, a missing candidate or a missing plan produce `not-run`;
none of those states is turned into native evidence.

The same command also writes `release-smoke.json`, the v2 report
directly consumable by the release flow. It holds the actual commands, the
declared assertions and the hashes of the receipts/stdout/stderr; it holds no keys,
secrets or custody paths. The file is not yet signed evidence.

To turn the raw report into a form the evidence producer
accepts:

```sh
python3 maintenance/tools/native_release_smoke.py \
  --candidate /path/candidate \
  --platform macOS-ARM64 \
  --handoff /path/local-handoff/release-smoke.json \
  --output /outside/release-smoke-normalized.json
```

The v1 handoff stays available for detailed audit and for the legacy local
aggregate. Only a `passed` handoff can feed the intermediate
aggregate:

```sh
python3 -m maintenance.tools.native_handoff_evidence aggregate \
  --candidate /path/candidate \
  --handoff /path/local-handoff/handoff.json \
  --expected-candidate-sha256 CANDIDATE_JSON_SHA256 \
  --output /outside/the/candidate/native-evidence-pending.json
```

The aggregate removes the content and paths of logs, commands, environment variables
and absolute runtime paths. It keeps only the candidate's
identity, non-sensitive chip/model, closed states, redacted receipts and the
digests needed for a future protected ceremony.

The file produced is always `status: pending`, `signed: false` and
`promotable: false`. It stays outside the immutable candidate, cannot be named
`release-evidence.json` and does not satisfy step F's promotion gate.

## Protected assembly of the evidence

The `native-m3.yml` workflow also validates `release-smoke.json` on the runner and
produces, outside the candidate, `records/macOS-ARM64.json` and
`release-evidence-body.json`. The `native-m3-input` artifact carries only those
two files, the plan and the pending aggregate; the raw logs and receipts stay in the
runner's private scratch.

The authorized maintainer signs the exact bytes of
`release-evidence-body.json` outside the repository and returns a public JSON
envelope with `body_sha256` and `{keyid,sig}`. In this project, the single maintainer
can run that ceremony under ADR 0007's waiver; that is not independent human
review. The `sign-native-evidence.yml` workflow checks the
provenance of the M3 run, downloads the candidate and the input, and runs:

```sh
python3 -m maintenance.tools.assemble_release_evidence assemble \
  --candidate candidate \
  --records-dir native-input/records \
  --body native-input/release-evidence-body.json \
  --signatures signatures.json \
  --trust-root m3-root.json \
  --output signed-evidence/release-evidence.json
```

The command has no mode to generate a signature and accepts no private key. Without
`--trust-root`, the CLI fails before producing the aggregate; after validation,
the `native-m3-signed` artifact is the only one the promotion workflow accepts.

# ADR 0004 — Preserve the upstream ezQuake stable bundle on macOS

- **Status:** accepted in PR 5's corrective code; not published
- **Date:** 2026-08-03
- **Issue:** [#50](https://github.com/x86dx2/x86qw/issues/50)
- **Scope:** ezQuake stable `3.6.9`, `macos-universal` variant

## Context

Up to public release `0.7.1`, installing, updating and repairing the stable
client changed `Contents/Info.plist` and ran
`codesign --force --deep --sign -`. That transformation removed the App Sandbox
and the hardened runtime from the received bundle, created a new local ad hoc
signature and made the receipt record the modified executable.

An ad hoc signature only proves the consistency of the content that was signed
locally; it does not authenticate the publisher. Apple requires a Developer ID,
the hardened runtime and other requirements for the normal notarization flow,
and explains that changing a bundle after it is signed invalidates its
signature:

- [Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution);
- [Resolving common notarization issues](https://developer.apple.com/documentation/security/resolving-common-notarization-issues);
- [Technical Note TN2206: macOS Code Signing In Depth](https://developer.apple.com/library/archive/technotes/tn2206/_index.html).

## Evidence about the pinned artifact

The official ZIP preserved in
`dist/clients/ezquake/stable/3.6.9/macos-universal/` has SHA-256
`2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed`.
Inspecting it on macOS produced:

| Evidence | Upstream stable 3.6.9 | Installation transformed by 0.7.1 |
|---|---|---|
| Executable | `14633b5d4201e9460250ad236fde2e4ad579a6ddbaf81301830099d8cf004f33` | `e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a` |
| `Info.plist` | `0600a1a09231ec9168fba74821af9c3ba8b6d1d97165c2ac566b4438c60deaf7` | `26988ec42a27b6a4427c13feca5df9ede8606c9dcd1529a89398334189679d34` |
| `CodeResources` | `a696d8b764e4c61aa6269b1dd931dab4e408786aa6c1fc25890dd56c942fd87d` | same hash |
| Architectures | `arm64` and `x86_64` | `arm64` and `x86_64` |
| Signature | ad hoc | ad hoc, redone locally |
| Hardened runtime | present | removed |
| App Sandbox | present | removed |
| Team Identifier | absent | absent |
| Stapled ticket | absent | absent |
| `spctl` | rejected | rejected |
| `NSPrefersDisplaySafeAreaCompatibilityMode` | absent | `false` |

`codesign --verify` only confirms the internal integrity of the observed
signature. The absence of a Team ID, a ticket and acceptance by `spctl` rules
out claiming a Developer ID or notarization.

The values in the table were obtained without changing the bundles. To repeat
the inspection, set `UPSTREAM_APP` to the `ezQuake.app` extracted from the
pinned ZIP and `LEGACY_APP` to the `ezQuake Stable.app` installed by 0.7.1,
then run:

```sh
shasum -a 256 \
  "$UPSTREAM_APP/Contents/MacOS/ezQuake" \
  "$UPSTREAM_APP/Contents/Info.plist" \
  "$UPSTREAM_APP/Contents/_CodeSignature/CodeResources" \
  "$LEGACY_APP/Contents/MacOS/ezQuake" \
  "$LEGACY_APP/Contents/Info.plist" \
  "$LEGACY_APP/Contents/_CodeSignature/CodeResources"
codesign -d --verbose=4 --entitlements :- "$UPSTREAM_APP"
codesign -d --verbose=4 --entitlements :- "$LEGACY_APP"
xcrun stapler validate "$UPSTREAM_APP"
spctl --assess --type execute --verbose=4 "$UPSTREAM_APP"
```

The last two commands fail for the observed artifact; that failure is evidence
of the bundle's state, not an instruction to get around macOS.

## Decision

x86QW preserves the upstream bundle's files in full on the stable path:

- it does not change `Info.plist`;
- it does not remove the sandbox or entitlements;
- it does not run `codesign --sign`, `--force` or `--deep` to sign;
- it does not remove the quarantine nor get around Gatekeeper;
- it may run `codesign --verify --deep --strict` on macOS to validate the
  consistency of the received bundle;
- it keeps version, channel, installed name and receipt separate from nightly.

The local transformation routine now accepts only the nightly channel. That
pre-existing exception stays outside the stable trust decision and remains
explicitly conditional until it gets its own work.

Support for the stable and nightly macOS clients is projected in the candidate
as `conditional`. Availability of the universal artifact is not the same as a
Developer ID, notarization or a complete native smoke.

## Migration of 0.7.1 installations

The transformed stable bundle is recognized only when all of these hold:

1. a valid receipt for the pinned stable 3.6.9 artifact;
2. the exact hash of the executable transformed by the 0.7.1 line;
3. the safe area key inserted by the old transformation;
4. on macOS, the confirmed absence of the sandbox.

An unknown artifact, an upstream executable, a modified executable with an
unrecognized hash, a missing receipt or an inconclusive identity is never
replaced by inference. When the old transformation is proven,
`update --dry-run` shows the restoration; the bootstrap fetches the same
recorded version, validates size and SHA-256, prepares the bundle without
mutating it and swaps runtime and receipt transactionally. A commit failure
restores both.

The installed CLI does not download payloads arbitrarily: `repair` diagnoses
and advises running the bootstrap again on the same destination. Personal
settings, PAKs, demos, logs, the nightly channel and newer versions are
preserved. Update and repair do not delete the security-scoped bookmark
already chosen; the first install may clear an old selection to force the
confirmation of the new destination.

## Rejected alternatives

1. **Keep the local ad hoc signature:** keeps losing the artifact's verifiable
   identity and removes upstream protections.
2. **Remove the quarantine:** gets around a system protection and does not
   create a publisher identity.
3. **Publish an ad hoc x86QW bundle now:** swaps one unauthenticated identity
   for another and does not solve Gatekeeper.
4. **Adopt a Developer ID without a key pipeline and governance:** would
   require the hardened runtime, notarization, stapling, custody and incident
   response; that option needs its own ADR and evidence.

## Consequences and residual risks

Preserving stable keeps x86QW from degrading the received bundle, but it does
not turn the upstream into software identified or notarized by Apple.
Gatekeeper may require explicit approval from the user.

The upstream sandbox may ask for the `quake-world` folder on the first launch
and reuse a security-scoped bookmark on later launches, following the contract
Apple documents in
[Accessing files from the macOS App Sandbox](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox).
The first and second launch, window/fullscreen, a notched display, arm64 and
Intel must be run with the immutable candidate in PR 11. Until then, support
stays conditional.

Release `0.7.1`, its hashes, tag, bundles and public pointers stay immutable.
This decision does not authorize publication.

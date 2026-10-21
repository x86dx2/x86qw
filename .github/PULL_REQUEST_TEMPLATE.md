## Result

<!-- What changes for the player, the operator or the maintainer? -->

## Context

<!-- Which problem or issue led to the change? -->

## Validation

- [ ] `go -C go test ./...` and `go -C go run ./cmd/x86qw-content verify`
- [ ] Affected commands checked with `x86qw COMMAND --help`
- [ ] `cd site && npm test && npm run deploy:dry-run`, when the site changes
- [ ] System the change was used on:
- [ ] README and `docs/` still describe what the code does

## Distribution and provenance

- [ ] No new distributed byte
- [ ] Or: origin, version, license, consumer, size and SHA-256 were recorded
- [ ] Upstream and x86QW customization stay separate
- [ ] Existing published artifacts were not changed

## Risk and rollback

<!-- What could regress and how to undo it safely? Remove secrets from logs. -->

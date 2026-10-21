# Release incident response

1. Stop whatever is publishing: cancel the running workflow and do not merge
   PRs that touch `site/public`. Do not overwrite tags or packages.
2. Keep logs, the catalog, the TUF metadata and the release assets, without
   secrets.
3. Check the public chain with `go -C go run ./cmd/x86qw-release monitor
   --checkout` and the authenticated catalog with `x86qw catalog`.
4. If a key is involved, follow the
   [compromise runbook](trust-compromise.md).
5. Publish the fix only with a new commit, a new CLI or package version and a
   new TUF generation; the problematic artifact stays immutable for audit.
6. Record the impact, the affected platforms and what failed in an issue.

Do not change PAKs or rewrite the provenance of upstream components during an
incident.

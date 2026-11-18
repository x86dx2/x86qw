# Shortcuts for the maintainer commands. `make` lists them.
# The tools run with `go run`, so each call uses the code checked out now.

CONTENT := go -C go run ./cmd/x86qw-content
RELEASE := go -C go run ./cmd/x86qw-release
# Components to limit check/update to, e.g. make check C="ktx mvdsv".
C ?=

.DEFAULT_GOAL := help
.PHONY: help check update-plan update verify publish sign release prepare monitor test lint e2e site-test ci

##@ Content
check: ## check upstreams without changing files (exit 2 = updates)
	$(CONTENT) check $(C)
update-plan: ## show the update plan without changing files
	$(CONTENT) update --dry-run $(C)
update: ## update the components that changed upstream
	$(CONTENT) update $(C)
verify: ## validate dist, inventories, catalog, TUF and LFS
	$(CONTENT) verify
publish: ## publish the artifacts to the GitHub and GitLab mirrors
	GITLAB_TOKEN="$$(glab config get token --host gitlab.com)" $(CONTENT) publish

##@ Release (docs/runbooks/release.md)
sign: ## sign the next TUF generation with the offline keys
	$(RELEASE) sign
release: ## build and publish the CLI binaries: make release V=X.Y.Z
	@test -n "$(V)" || { echo "usage: make release V=X.Y.Z" >&2; exit 2; }
	gh workflow run go-release.yml --ref main -f version=$(V)
prepare: ## branch and prepare the release PR: make prepare V=X.Y.Z
	@test -n "$(V)" || { echo "usage: make prepare V=X.Y.Z" >&2; exit 2; }
	git fetch -q origin && git switch -c release/cli-$(V) origin/main
	$(RELEASE) prepare --version $(V)
monitor: ## check the published chain against this checkout
	$(RELEASE) monitor --checkout

##@ Tests
test: ## run the Go tests
	go -C go test ./...
lint: ## gofmt, vet and staticcheck, as the CI runs them
	test -z "$$(gofmt -l go)"
	go -C go vet ./...
	go -C go run honnef.co/go/tools/cmd/staticcheck@v0.8.2 ./...
e2e: ## network and real-install tests (slow)
	cd go && X86QW_NETWORK_TESTS=1 X86QW_REAL_INSTALL=1 X86QW_NO_MACOS_PREFERENCES=1 \
		go test -count=1 -timeout 45m ./internal/setup/... ./cmd/x86qw/...
site-test: ## site tests
	npm --prefix site test

##@ CI
ci: ## latest run of each workflow
	@for w in $$(ls .github/workflows); do gh run list -w $$w -L 1 --json conclusion,status,displayTitle,createdAt \
		--jq ".[] | \"$$w\t\(.conclusion // .status)\t\(.createdAt[:16])\t\(.displayTitle)\""; done | column -t -s "$$(printf '\t')"

help:
	@awk 'BEGIN {FS = ":.*## "} /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} /^[a-z0-9-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo

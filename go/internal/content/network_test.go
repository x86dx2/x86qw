package content

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Offline fakes for every network command: an httptest handler behind a
// RoundTripper, so no request ever leaves the process, and a recorder in
// place of git/gh.

type fakeWeb struct {
	mu       sync.Mutex
	handler  http.HandlerFunc
	requests []string
}

func (f *fakeWeb) RoundTrip(request *http.Request) (*http.Response, error) {
	f.mu.Lock()
	f.requests = append(f.requests, request.Method+" "+request.URL.String())
	f.mu.Unlock()
	recorder := httptest.NewRecorder()
	f.handler(recorder, request)
	response := recorder.Result()
	response.Request = request
	return response, nil
}

func (f *fakeWeb) client() *http.Client { return &http.Client{Transport: f} }

type recorder struct {
	mu       sync.Mutex
	commands []string
	reply    func(args []string) ([]byte, error)
}

func (r *recorder) run(_ string, _ []byte, name string, args ...string) ([]byte, error) {
	r.mu.Lock()
	r.commands = append(r.commands, name+" "+strings.Join(args, " "))
	r.mu.Unlock()
	if r.reply != nil {
		return r.reply(append([]string{name}, args...))
	}
	return nil, nil
}

func writeJSONResponse(w http.ResponseWriter, value any) {
	payload, _ := pyjson.Encode(value, false)
	w.Header().Set("Content-Type", "application/json")
	w.Write(payload)
}

// currentUpstreams serves the exact upstream state the checkout pins.
func currentUpstreams(t *testing.T, root string, extraNightly string) *fakeWeb {
	t.Helper()
	project := NewProject(root)
	manifest, err := LoadManifest(project.Path(ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	files := manifest.Value("files").(*Object)
	releases, err := project.Releases()
	if err != nil {
		t.Fatal(err)
	}
	revision := pyjson.Repr(pyjson.Get(releases, "reference", "revision"))
	const tree = "1111111111111111111111111111111111111111"
	var entries []any
	sizes := map[string]int64{}
	bodies := map[string]string{} // url -> dist path for GET
	for _, relative := range files.Keys() {
		metadata := files.Value(relative).(*Object)
		url := pyjson.Repr(metadata.Value("url"))
		sizes[url] = int64Of(metadata.Value("size"))
		bodies[url] = relative
		if parts, ok := nquakeSnapshotParts(relative); ok {
			digest, err := GitBlobSHA1(filepath.Join(root, "dist", filepath.FromSlash(relative)))
			if err != nil {
				t.Fatal(err)
			}
			entries = append(entries, object("path", strings.Join(parts[3:], "/"), "mode", "100644", "type", "blob",
				"sha", digest, "size", metadata.Value("size")))
		}
	}
	latest := map[string]string{"QW-Group/ezquake-source": "3.6.9"}
	components := releases.Value("components").(*Object)
	for _, identifier := range components.Keys() {
		release := components.Value(identifier).(*Object)
		if repository, ok := pyjson.Get(release, "upstream", "repository").(string); ok {
			latest[repository] = pyjson.Repr(pyjson.Get(release, "upstream", "release"))
		}
		for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
			bodies[pyjson.Repr(artifact.Value("url"))] = pyjson.Repr(artifact.Value("distribution_path"))
		}
	}
	nightlies := map[string][]string{}
	for _, relative := range files.Keys() {
		if strings.HasPrefix(relative, "clients/ezquake/nightly/") && !strings.Contains(relative, "/source/") {
			url := pyjson.Repr(files.Value(relative).(*Object).Value("url"))
			directory := url[:strings.LastIndex(url, "/")+1]
			nightlies[directory] = append(nightlies[directory], posixName(relative))
		}
	}
	web := &fakeWeb{}
	web.handler = func(w http.ResponseWriter, r *http.Request) {
		full := "https://" + r.URL.Host + r.URL.Path
		switch {
		case r.URL.Host == "api.github.com" && r.URL.Path == "/repos/nQuake/distfiles/git/ref/heads/master":
			writeJSONResponse(w, object("ref", "refs/heads/master", "object", object("type", "commit", "sha", revision,
				"url", "https://api.github.com/repos/nQuake/distfiles/git/commits/"+revision)))
		case r.URL.Host == "api.github.com" && r.URL.Path == "/repos/nQuake/distfiles/git/commits/"+revision:
			writeJSONResponse(w, object("sha", revision, "tree", object("sha", tree, "url", "https://api.github.com/repos/nQuake/distfiles/git/trees/"+tree)))
		case r.URL.Host == "api.github.com" && r.URL.Path == "/repos/nQuake/distfiles/git/trees/"+tree && r.URL.RawQuery == "recursive=1":
			writeJSONResponse(w, object("sha", tree, "truncated", false, "tree", entries))
		case r.URL.Host == "github.com" && strings.HasSuffix(r.URL.Path, "/releases/latest"):
			repository := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/"), "/releases/latest")
			tag, ok := latest[repository]
			if !ok {
				http.NotFound(w, r)
				return
			}
			http.Redirect(w, r, "https://github.com/"+repository+"/releases/tag/"+tag, http.StatusFound)
		case r.URL.Host == "github.com" && strings.Contains(r.URL.Path, "/releases/tag/"):
			w.WriteHeader(http.StatusOK)
		case nightlies[full] != nil:
			var page strings.Builder
			page.WriteString("<html><body>")
			for _, name := range append(append([]string{}, nightlies[full]...), extraNightlyFor(full, extraNightly)...) {
				fmt.Fprintf(&page, `<a href="%s">%s</a>`, name, name)
			}
			page.WriteString(`<a href="../">..</a></body></html>`)
			w.Write([]byte(page.String()))
		case r.Method == http.MethodHead && sizes[full] > 0:
			w.Header().Set("Content-Length", fmt.Sprint(sizes[full]))
		case r.Method == http.MethodHead && extraNightly != "" && strings.Contains(full, extraNightly):
			w.Header().Set("Content-Length", "1234")
		case r.Method == http.MethodGet && bodies[full] != "":
			payload, err := os.ReadFile(filepath.Join(root, "dist", filepath.FromSlash(bodies[full])))
			if err != nil {
				http.NotFound(w, r)
				return
			}
			w.Write(payload)
		default:
			http.NotFound(w, r)
		}
	}
	return web
}

func unmirroredReleaseArtifacts(t *testing.T, project *Project) []string {
	t.Helper()
	manifest, err := LoadManifest(project.Path(ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	releases, _ := project.Releases()
	files := manifest.Value("files").(*Object)
	var paths []string
	components := releases.Value("components").(*Object)
	for _, identifier := range components.Keys() {
		for _, artifact := range objects(getOr(components.Value(identifier).(*Object), "artifacts", []any{})) {
			if path := pyjson.Repr(artifact.Value("distribution_path")); !files.Has(path) {
				paths = append(paths, path)
			}
		}
	}
	return paths
}

func extraNightlyFor(directory, build string) []string {
	if build == "" {
		return nil
	}
	for fragment, suffix := range map[string]string{
		"/macOS/universal/": "_ezQuake-macOS-universal.zip",
		"/linux/x86_64/":    "_ezQuake-x86_64.AppImage",
		"/windows/x64/":     "_ezquake.exe",
	} {
		if strings.Contains(directory, fragment) {
			return []string{build + suffix}
		}
	}
	return nil
}

func TestCheckAndUpdateAgainstFakeUpstreams(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)

	web := currentUpstreams(t, root, "")
	project := NewProject(root)
	project.HTTP = web.client()
	var out, info bytes.Buffer
	code, err := project.Check(&out, &info, false, false)
	if err != nil {
		t.Fatalf("check: %v\n%s", err, out.String())
	}
	// Release artifacts that are packaged but not mirrored in dist/manifest.json
	// are reported as new distribution files, exactly like manage.py check.
	unmirrored := unmirroredReleaseArtifacts(t, project)
	expectedCode := 0
	if len(unmirrored) > 0 {
		expectedCode = 2
	}
	summary := fmt.Sprintf("\n0 componente(s) e %d arquivo(s) envolvidos em mudancas.", len(unmirrored))
	if code != expectedCode || !strings.Contains(out.String(), summary) {
		t.Fatalf("check com upstreams atuais inesperado (exit %d):\n%s", code, out.String())
	}
	for _, path := range unmirrored {
		if !strings.Contains(out.String(), "[ATUALIZAR] "+path+" (novo)") {
			t.Fatalf("artefato de release sem espelho não reportado: %s", path)
		}
	}
	for _, request := range web.requests {
		if strings.HasPrefix(request, "PUT") || strings.HasPrefix(request, "POST") || strings.HasPrefix(request, "PATCH") {
			t.Fatalf("check fez uma escrita remota: %s", request)
		}
	}

	update := NewProject(root)
	update.HTTP = web.client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true}); err != nil {
		t.Fatalf("update --dry-run: %v\n%s", err, out.String())
	}
	expected := "[OK] O dist e os inventarios ja representam os upstreams atuais."
	if len(unmirrored) > 0 {
		expected = fmt.Sprintf("[INFO] %d alteracao(oes) de arquivo; nQuake", len(unmirrored))
	}
	if !strings.Contains(out.String(), expected) {
		t.Fatalf("update --dry-run inesperado:\n%s", out.String())
	}

	// A newer nightly is discovered but never trusted without a review.
	newer := currentUpstreams(t, root, "20990101-000000_abcdef0")
	project = NewProject(root)
	project.HTTP = newer.client()
	out.Reset()
	code, err = project.Check(&out, &info, false, true)
	if err != nil {
		t.Fatal(err)
	}
	if code != 2 {
		t.Fatalf("check deveria sair 2 com nightly novo: %d", code)
	}
	report, err := pyjson.Decode(out.Bytes(), pyjson.RejectDuplicates)
	if err != nil {
		t.Fatalf("check --json inválido: %v\n%s", err, out.String())
	}
	review, _ := pyjson.StrList(pyjson.Get(report, "review_required"))
	if len(review) != 3 || !strings.Contains(review[0], "/nightly/20990101-000000_abcdef0/") {
		t.Fatalf("review_required inesperado: %v", review)
	}
	update = NewProject(root)
	update.HTTP = newer.client()
	out.Reset()
	err = update.Update(&out, UpdateOptions{Workers: 4, DryRun: true})
	if err == nil || !strings.Contains(err.Error(), "exige(m) tamanho e SHA-256 revisados") {
		t.Fatalf("update deveria recusar candidato sem pin: %v", err)
	}
}

// publishFixture is a minimal project with one content package.
func publishFixture(t *testing.T, payload []byte, withGitLab bool) (*Project, string) {
	t.Helper()
	source := repoRoot(t)
	root := t.TempDir()
	policy, _ := os.ReadFile(filepath.Join(source, PolicyPath))
	os.MkdirAll(filepath.Join(root, filepath.Dir(PolicyPath)), 0o755)
	os.WriteFile(filepath.Join(root, PolicyPath), policy, 0o644)
	filename := "nquake-maps-test.zip"
	digest := sha256.Sum256(payload)
	mirror := GitHubReleaseURL("nquake-test", filename)
	urls := []any{mirror}
	if withGitLab {
		urls = append(urls, GitLabPackageURL("nquake-maps", "test", filename))
	}
	pkg := object("component", "nquake", "package", "nquake-maps", "version", "test", "channel", "content",
		"platform", "any", "architecture", "any", "filename", filename, "size", intNumber(len(payload)),
		"sha256", hex.EncodeToString(digest[:]), "origin_url", mirror, "license", "upstream-distfiles-terms",
		"license_url", "https://github.com/nQuake/distfiles", "source_urls", []any{"https://github.com/nQuake/distfiles"},
		"redistribution_reviewed", true, "urls", urls, "mirror_title", "x86QW Content · teste",
		"mirror_notes", "Pacotes de teste.", "mirror_latest", false)
	catalog := object("format", pyjson.Number("1"), "project", "x86qw", "generated_at", "2026-01-01T00:00:00Z", "packages", []any{pkg})
	if err := WriteJSON(filepath.Join(root, CatalogPath), catalog); err != nil {
		t.Fatal(err)
	}
	build := filepath.Join(root, BuildsDir, "components-test", filename)
	os.MkdirAll(filepath.Dir(build), 0o755)
	os.WriteFile(build, payload, 0o644)
	return NewProject(root), filename
}

func TestPublishAgainstFakeMirrors(t *testing.T) {
	payload := []byte("x86qw package bytes")
	project, filename := publishFixture(t, payload, false)
	digest := sha256.Sum256(payload)
	gitlabURL := GitLabPackageURL("nquake-maps", "test", filename)
	var mu sync.Mutex
	releaseExists, uploaded := false, false
	var gitlab []byte
	var gitlabToken string
	web := &fakeWeb{handler: func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		full := "https://" + r.URL.Host + r.URL.Path
		switch {
		case full == "https://api.github.com/repos/x86dx2/x86qw/releases/latest":
			writeJSONResponse(w, object("tag_name", "x86qw-installer-1.0.16"))
		case full == "https://api.github.com/repos/x86dx2/x86qw/releases/tags/nquake-test":
			if !releaseExists {
				http.NotFound(w, r)
				return
			}
			var assets []any
			if uploaded {
				assets = append(assets, object("name", filename, "size", intNumber(len(payload)), "digest", "sha256:"+hex.EncodeToString(digest[:])))
			}
			writeJSONResponse(w, object("id", pyjson.Number("7"), "name", "x86QW Content · teste", "body", "Pacotes de teste.", "assets", orEmpty(assets)))
		case full == gitlabURL && r.Method == http.MethodGet:
			if gitlab == nil {
				http.NotFound(w, r)
				return
			}
			w.Write(gitlab)
		case full == gitlabURL && r.Method == http.MethodPut:
			body := new(bytes.Buffer)
			body.ReadFrom(r.Body)
			gitlab = body.Bytes()
			gitlabToken = r.Header.Get("PRIVATE-TOKEN")
			w.WriteHeader(http.StatusCreated)
		default:
			http.NotFound(w, r)
		}
	}}
	runner := &recorder{reply: func(args []string) ([]byte, error) {
		mu.Lock()
		defer mu.Unlock()
		switch strings.Join(args[:3], " ") {
		case "gh release create":
			releaseExists = true
		case "gh release upload":
			uploaded = true
		}
		return nil, nil
	}}
	project.HTTP, project.Run = web.client(), runner.run
	project.Getenv = func(name string) string {
		return map[string]string{"GH_TOKEN": "ghs_test", "GLAB_TOKEN": "glpat-test"}[name]
	}

	var out bytes.Buffer
	if err := project.Publish(&out, false, false, true); err != nil {
		t.Fatalf("publish --dry-run: %v", err)
	}
	if len(runner.commands) != 0 || gitlab != nil {
		t.Fatalf("dry-run executou escrita: %v", runner.commands)
	}
	if !strings.Contains(out.String(), "[GITHUB x86dx2/x86qw] criar release nquake-test") {
		t.Fatalf("plano inesperado:\n%s", out.String())
	}

	out.Reset()
	if err := project.Publish(&out, false, false, false); err != nil {
		t.Fatalf("publish: %v\n%s", err, out.String())
	}
	if len(runner.commands) != 2 || !strings.HasPrefix(runner.commands[0], "gh release create nquake-test --repo x86dx2/x86qw") ||
		!strings.HasPrefix(runner.commands[1], "gh release upload nquake-test ") {
		t.Fatalf("comandos gh inesperados: %v", runner.commands)
	}
	if !bytes.Equal(gitlab, payload) || gitlabToken != "glpat-test" {
		t.Fatalf("upload GitLab inesperado: %q %q", gitlab, gitlabToken)
	}
	catalog, _ := LoadJSONObject(project.Path(CatalogPath))
	urls, _ := pyjson.StrList(objects(catalog.Value("packages"))[0].Value("urls"))
	if len(urls) != 2 || urls[1] != gitlabURL || catalog.Value("generated_at") == "2026-01-01T00:00:00Z" {
		t.Fatalf("catálogo não registrou o mirror GitLab: %v", urls)
	}

	// A second publication only verifies.
	runner.commands = nil
	out.Reset()
	if err := project.Publish(&out, false, false, false); err != nil {
		t.Fatal(err)
	}
	if len(runner.commands) != 0 || !strings.Contains(out.String(), "catálogo já continha os 1 mirrors GitLab") {
		t.Fatalf("republicação não foi idempotente: %v\n%s", runner.commands, out.String())
	}
}

func TestPublishRejectsChangedMirror(t *testing.T) {
	project, filename := publishFixture(t, []byte("published"), true)
	web := &fakeWeb{handler: func(w http.ResponseWriter, r *http.Request) {
		switch "https://" + r.URL.Host + r.URL.Path {
		case GitLabPackageURL("nquake-maps", "test", filename):
			w.Write([]byte("different"))
		default:
			http.NotFound(w, r)
		}
	}}
	project.HTTP, project.Run = web.client(), (&recorder{}).run
	err := project.Publish(&bytes.Buffer{}, false, true, false)
	if err == nil || !strings.Contains(err.Error(), "divergente") {
		t.Fatalf("mirror divergente aceito: %v", err)
	}
}

func TestGitLabUploadNeverFollowsRedirects(t *testing.T) {
	project, filename := publishFixture(t, []byte("published"), false)
	url := GitLabPackageURL("nquake-maps", "test", filename)
	web := &fakeWeb{handler: func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPut {
			http.Redirect(w, r, "https://evil.example/collect", http.StatusTemporaryRedirect)
			return
		}
		http.NotFound(w, r)
	}}
	project.HTTP = web.client()
	project.Getenv = func(string) string { return "glpat-secret" }
	err := project.uploadGitLab(project.Path(BuildsDir+"/components-test/"+filename), url)
	if err == nil || !strings.Contains(err.Error(), "HTTP status 307") {
		t.Fatalf("redirecionamento do upload aceito: %v", err)
	}
	for _, request := range web.requests {
		if strings.Contains(request, "evil.example") {
			t.Fatalf("token seguiu o redirecionamento: %v", web.requests)
		}
	}
	if len(web.requests) != 1 {
		t.Fatalf("requisições inesperadas: %v", web.requests)
	}
}

func TestCommitSequence(t *testing.T) {
	project := NewProject(t.TempDir())
	runner := &recorder{reply: func(args []string) ([]byte, error) {
		switch strings.Join(args, " ") {
		case "git status --porcelain":
			return []byte(" M dist/manifest.json\n"), nil
		case "git diff --cached --name-only":
			return []byte("dist/manifest.json\n"), nil
		case "git remote":
			return []byte("origin\ngitlab\n"), nil
		}
		return nil, nil
	}}
	project.Run = runner.run
	if err := project.Commit(&bytes.Buffer{}, "", true); err != nil {
		t.Fatal(err)
	}
	expected := []string{
		"git status --porcelain",
		"git add dist maintenance/inventory maintenance/recipes site/public/api/v1/catalog.json",
		"git diff --cached --name-only",
		"git commit -m chore(distribution): update preserved upstreams",
		"git push origin HEAD",
		"git remote",
		"git push gitlab HEAD",
	}
	if strings.Join(runner.commands, "\n") != strings.Join(expected, "\n") {
		t.Fatalf("sequência git inesperada:\n%s", strings.Join(runner.commands, "\n"))
	}

	clean := &recorder{}
	project.Run = clean.run
	var out bytes.Buffer
	if err := project.Commit(&out, "", true); err != nil || len(clean.commands) != 1 || !strings.Contains(out.String(), "Nao ha alteracoes") {
		t.Fatalf("commit sem mudanças: %v %v", err, clean.commands)
	}
}

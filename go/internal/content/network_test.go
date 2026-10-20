package content

import (
	"bytes"
	"crypto/sha1"
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
	nquake   *fakeNQuake
}

// fakeNQuake is the nQuake repository a fake serves: every file of the
// inventory, with the consumed ones as the checkout pins them, its Git tree
// and its manifest.json.
type fakeNQuake struct {
	revision string
	files    map[string]NQuakeFile
	local    map[string]string // consumed path -> file in dist
	data     map[string][]byte // path -> content that replaces local
}

func (n *fakeNQuake) tree() string {
	sum := sha1.Sum([]byte(n.revision))
	return hex.EncodeToString(sum[:])
}

func (n *fakeNQuake) treeEntries() []any {
	var entries []any
	for _, path := range sortedKeys(n.files) {
		file := n.files[path]
		entries = append(entries, object("path", path, "mode", "100644", "type", "blob", "sha", file.GitSHA1,
			"size", pyjson.Number(fmt.Sprint(file.Size))))
	}
	return entries
}

func (n *fakeNQuake) manifest() *Object {
	packages := pyjson.NewObject()
	for _, path := range sortedKeys(n.files) {
		name, relative, _ := strings.Cut(path, "/")
		group, _ := packages.Value(name).(*Object)
		if group == nil {
			group = object("bytes", pyjson.Number("0"), "files", []any{})
			packages.Set(name, group)
		}
		file := n.files[path]
		group.Set("files", append(group.Value("files").([]any), object("path", relative,
			"size", pyjson.Number(fmt.Sprint(file.Size)), "sha256", file.SHA256)))
	}
	return object("schema", pyjson.Number("1"), "commit", n.revision, "generated", "2026-10-20T00:00:00Z", "packages", packages)
}

func (n *fakeNQuake) body(path string) ([]byte, bool) {
	if data, ok := n.data[path]; ok {
		return data, true
	}
	if source, ok := n.local[path]; ok {
		data, err := os.ReadFile(source)
		return data, err == nil
	}
	return nil, false
}

func gitBlob(data []byte) string {
	digest := sha1.New()
	fmt.Fprintf(digest, "blob %d\x00", len(data))
	digest.Write(data)
	return hex.EncodeToString(digest.Sum(nil))
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
	// The fake nQuake head is the revision the inventory last reviewed.
	inventory, err := LoadNQuakeInventory(filepath.Join(root, NQuakeInventoryPath))
	if err != nil {
		t.Fatal(err)
	}
	revision := inventory.Revision
	nquake := &fakeNQuake{revision: revision, files: map[string]NQuakeFile{}, local: map[string]string{}, data: map[string][]byte{}}
	for path, file := range inventory.Files {
		nquake.files[path] = file
	}
	sizes := map[string]int64{}
	bodies := map[string]string{} // url -> dist path for GET
	for _, relative := range files.Keys() {
		metadata := files.Value(relative).(*Object)
		url := pyjson.Repr(metadata.Value("url"))
		sizes[url] = int64Of(metadata.Value("size"))
		bodies[url] = relative
		if parts, ok := nquakeSnapshotParts(relative); ok {
			local := filepath.Join(root, "dist", filepath.FromSlash(relative))
			digest, err := GitBlobSHA1(local)
			if err != nil {
				t.Fatal(err)
			}
			upstream := strings.Join(parts[3:], "/")
			nquake.files[upstream] = NQuakeFile{int64Of(metadata.Value("size")), pyjson.Repr(metadata.Value("sha256")), digest}
			nquake.local[upstream] = local
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
	web := &fakeWeb{nquake: nquake}
	web.handler = func(w http.ResponseWriter, r *http.Request) {
		full := "https://" + r.URL.Host + r.URL.Path
		switch {
		case r.URL.Host == "api.github.com" && r.URL.Path == "/repos/nQuake/distfiles/git/ref/heads/master":
			writeJSONResponse(w, object("ref", "refs/heads/master", "object", object("type", "commit", "sha", nquake.revision,
				"url", "https://api.github.com/repos/nQuake/distfiles/git/commits/"+nquake.revision)))
		case r.URL.Host == "api.github.com" && r.URL.Path == "/repos/nQuake/distfiles/git/commits/"+nquake.revision:
			writeJSONResponse(w, object("sha", nquake.revision, "tree", object("sha", nquake.tree(), "url", "https://api.github.com/repos/nQuake/distfiles/git/trees/"+nquake.tree())))
		case r.URL.Host == "api.github.com" && r.URL.Path == "/repos/nQuake/distfiles/git/trees/"+nquake.tree() && r.URL.RawQuery == "recursive=1":
			writeJSONResponse(w, object("sha", nquake.tree(), "truncated", false, "tree", nquake.treeEntries()))
		case r.URL.Host == "raw.githubusercontent.com" && r.URL.Path == "/nQuake/distfiles/"+nquake.revision+"/manifest.json":
			writeJSONResponse(w, nquake.manifest())
		case r.URL.Host == "raw.githubusercontent.com" && strings.HasPrefix(r.URL.Path, "/nQuake/distfiles/"+nquake.revision+"/"):
			data, ok := nquake.body(strings.TrimPrefix(r.URL.Path, "/nQuake/distfiles/"+nquake.revision+"/"))
			if !ok {
				http.NotFound(w, r)
				return
			}
			w.Write(data)
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
	code, err := project.Check(&out, &info, false, false, nil)
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
		expected = fmt.Sprintf("[INFO] %d alteracao(oes) de arquivo.", len(unmirrored))
	}
	if !strings.Contains(out.String(), expected) {
		t.Fatalf("update --dry-run inesperado:\n%s", out.String())
	}

	// A newer nightly is discovered but never trusted without a review.
	newer := currentUpstreams(t, root, "20990101-000000_abcdef0")
	project = NewProject(root)
	project.HTTP = newer.client()
	out.Reset()
	code, err = project.Check(&out, &info, false, true, nil)
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

// limited answers 429 for every URL that matches, like web.archive.org did
// for pro-x, and serves everything else from the current upstreams.
func limited(web *fakeWeb, matches func(*http.Request) bool) *fakeWeb {
	upstream := web.handler
	web.handler = func(w http.ResponseWriter, r *http.Request) {
		if matches(r) {
			http.Error(w, "Too Many Requests", http.StatusTooManyRequests)
			return
		}
		upstream(w, r)
	}
	return web
}

func TestEachComponentIsCheckedOnItsOwn(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	archive := func(r *http.Request) bool { return r.URL.Host == "web.archive.org" }

	project := NewProject(root)
	project.HTTP = limited(currentUpstreams(t, root, ""), archive).client()
	var out, info bytes.Buffer
	code, err := project.Check(&out, &info, false, false, []string{"pro-x", "ktx"})
	if err != nil {
		t.Fatalf("check: %v\n%s", err, out.String())
	}
	if code != 1 || !strings.Contains(out.String(), "[INDISPONIVEL] pro-x: ") || !strings.Contains(out.String(), "[OK] ktx: ") {
		t.Fatalf("pro-x indisponivel deveria afetar so pro-x (exit %d):\n%s", code, out.String())
	}
	if strings.Contains(out.String(), "mvdsv") || strings.Contains(out.String(), "ezquake") {
		t.Fatalf("check selecionado reportou componentes fora da selecao:\n%s", out.String())
	}

	// A new nightly elsewhere still counts as an update, and the unavailable
	// upstream is listed without hiding it.
	project = NewProject(root)
	project.HTTP = limited(currentUpstreams(t, root, "20990101-000000_abcdef0"), archive).client()
	out.Reset()
	if code, err = project.Check(&out, &info, false, true, nil); err != nil || code != 2 {
		t.Fatalf("check deveria sair 2 com nightly novo e pro-x indisponivel: %d %v", code, err)
	}
	report, err := pyjson.Decode(out.Bytes(), pyjson.RejectDuplicates)
	if err != nil {
		t.Fatal(err)
	}
	statuses := map[string]string{}
	for _, item := range objects(pyjson.Get(report, "components")) {
		statuses[pyjson.Repr(item.Value("component"))] = pyjson.Repr(item.Value("status"))
	}
	if statuses["pro-x"] != "unavailable" || statuses["ktx"] != "current" || statuses["mvdsv"] != "current" {
		t.Fatalf("status por componente inesperado: %v", statuses)
	}

	// Selecting a component outside ezQuake ignores the unreviewed nightly.
	project = NewProject(root)
	project.HTTP = currentUpstreams(t, root, "20990101-000000_abcdef0").client()
	out.Reset()
	if code, err = project.Check(&out, &info, false, false, []string{"ktx"}); err != nil || code != 0 {
		t.Fatalf("check ktx nao deveria ver o nightly do ezQuake: %d %v\n%s", code, err, out.String())
	}
	update := NewProject(root)
	update.HTTP = currentUpstreams(t, root, "20990101-000000_abcdef0").client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true, Components: []string{"ktx"}}); err != nil {
		t.Fatalf("update ktx nao deveria depender do ezQuake: %v\n%s", err, out.String())
	}
	if !strings.Contains(out.String(), "[OK] ezQuake nightly: ") {
		t.Fatalf("update ktx tocou o ezQuake:\n%s", out.String())
	}

	// Explicitly selecting an unavailable component fails; selecting
	// everything skips it and keeps going.
	update = NewProject(root)
	update.HTTP = limited(currentUpstreams(t, root, ""), archive).client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true, Components: []string{"pro-x"}}); err == nil {
		t.Fatalf("update pro-x deveria falhar com upstream indisponivel:\n%s", out.String())
	}
	update = NewProject(root)
	update.HTTP = limited(currentUpstreams(t, root, ""), archive).client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true}); err != nil || !strings.Contains(out.String(), "[PULADO] pro-x: ") {
		t.Fatalf("update geral deveria pular pro-x: %v\n%s", err, out.String())
	}

	// nQuake down: its components are unavailable, ezQuake is still checked.
	nquake := func(r *http.Request) bool { return strings.HasPrefix(r.URL.Path, "/repos/nQuake/") }
	project = NewProject(root)
	project.HTTP = limited(currentUpstreams(t, root, ""), nquake).client()
	out.Reset()
	if code, err = project.Check(&out, &info, false, false, []string{"nquake-maps", "ezquake"}); err != nil || code != 1 {
		t.Fatalf("check com nQuake fora: %d %v\n%s", code, err, out.String())
	}
	if !strings.Contains(out.String(), "[INDISPONIVEL] nquake-maps: ") || strings.Contains(out.String(), "[INDISPONIVEL] ezquake") {
		t.Fatalf("nQuake fora deveria afetar so seus componentes:\n%s", out.String())
	}

	if _, err := NewProject(root).Check(&out, &info, true, false, []string{"nao-existe"}); err == nil || !strings.Contains(err.Error(), "desconhecido") {
		t.Fatalf("componente desconhecido deveria ser recusado: %v", err)
	}
}

// movedReference moves the fake nQuake to a new revision in which only
// changedFile, a file of x86qw-client-bootstrap, changed to edit(original).
const changedFile = "non-gpl/qw/nquake_default.cfg"

const movedRevision = "2222222222222222222222222222222222222222"

func movedReference(web *fakeWeb, edit func([]byte) []byte) *fakeWeb {
	original, _ := web.nquake.body(changedFile)
	data := edit(original)
	web.nquake.revision = movedRevision
	web.nquake.data[changedFile] = data
	web.nquake.files[changedFile] = NQuakeFile{int64(len(data)), sha256Hex(data), gitBlob(data)}
	return web
}

// appendComment changes the file without touching what x86QW replaces.
func appendComment(data []byte) []byte {
	return append(append([]byte{}, data...), "\n// nQuake mudou\n"...)
}

func TestEachComponentKeepsItsOwnNQuakeRevision(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	var out, info bytes.Buffer

	// Only the component whose consumed file changed is reported.
	project := NewProject(root)
	project.HTTP = movedReference(currentUpstreams(t, root, ""), appendComment).client()
	code, err := project.Check(&out, &info, false, true, nil)
	if err != nil || code != 2 {
		t.Fatalf("check com nQuake movido: %d %v\n%s", code, err, out.String())
	}
	report, err := pyjson.Decode(out.Bytes(), pyjson.RejectDuplicates)
	if err != nil {
		t.Fatal(err)
	}
	changed, _ := pyjson.StrList(pyjson.Get(report, "reference", "changed_components"))
	if len(changed) != 1 || changed[0] != "x86qw-client-bootstrap" {
		t.Fatalf("componentes alterados pelo nQuake: %v", changed)
	}
	statuses := map[string]string{}
	for _, item := range objects(pyjson.Get(report, "components")) {
		statuses[pyjson.Repr(item.Value("component"))] = pyjson.Repr(item.Value("status"))
	}
	if statuses["x86qw-client-bootstrap"] != "update-available" || statuses["nquake-maps"] != "current" || statuses["team-fortress"] != "current" {
		t.Fatalf("status por componente: %v", statuses)
	}
	for _, item := range objects(pyjson.Get(report, "distribution")) {
		path := pyjson.Repr(item.Value("path"))
		if parts, ok := nquakeSnapshotParts(path); ok && parts[2] == movedRevision {
			continue
		}
		if parts, ok := nquakeSnapshotParts(path); ok {
			manifest, _ := LoadManifest(project.Path(ManifestPath))
			if pyjson.Get(manifest, "files", path, "package") != "x86qw-client-bootstrap" {
				t.Fatalf("arquivo nQuake de outro componente mudou: %s (%v)", path, parts)
			}
		}
	}

	// update moves it alone and re-pins the x86QW replacements that still
	// apply; the others and their files stay as they are.
	update := NewProject(root)
	update.HTTP = movedReference(currentUpstreams(t, root, ""), appendComment).client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true}); err != nil {
		t.Fatalf("update geral com nQuake movido: %v\n%s", err, out.String())
	}
	releases, _ := update.Releases()
	pinned := ReferenceRevisionOf(releases, "x86qw-client-bootstrap")[:12]
	if !strings.Contains(out.String(), "[NQUAKE] x86qw-client-bootstrap: "+pinned+" -> 222222222222 (1 arquivo(s) do nQuake alterado(s); 3 customizacao(oes) x86QW reaplicada(s))") ||
		!strings.Contains(out.String(), "snapshot nQuake: "+pinned+" (12 removidos) -> 222222222222 (12 novos)") ||
		!strings.Contains(out.String(), "[INFO] Inventario nQuake: ") {
		t.Fatalf("update geral deveria mover so o bootstrap:\n%s", out.String())
	}
	update = NewProject(root)
	update.HTTP = movedReference(currentUpstreams(t, root, ""), appendComment).client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true, Components: []string{"nquake-maps", "ktx"}}); err != nil ||
		!strings.Contains(out.String(), "[OK] O dist e os inventarios ja representam os upstreams atuais.") {
		t.Fatalf("componentes sem mudanca no nQuake deveriam ficar como estao: %v\n%s", err, out.String())
	}

	// A replacement that no longer applies stops only that component.
	broken := func(data []byte) []byte {
		return bytes.Replace(data, []byte("gl_max_size                          \"32768\""), []byte("gl_max_size 4096"), 1)
	}
	update = NewProject(root)
	update.HTTP = movedReference(currentUpstreams(t, root, ""), broken).client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true}); err != nil ||
		!strings.Contains(out.String(), "[PULADO] x86qw-client-bootstrap: nQuake 222222222222: a substituicao x86QW em non-gpl/qw/nquake_default.cfg nao se aplica mais") ||
		strings.Contains(out.String(), "snapshot nQuake") {
		t.Fatalf("update deveria pular so o bootstrap com substituicao quebrada: %v\n%s", err, out.String())
	}
	update = NewProject(root)
	update.HTTP = movedReference(currentUpstreams(t, root, ""), broken).client()
	out.Reset()
	err = update.Update(&out, UpdateOptions{Workers: 4, DryRun: true, Components: []string{"x86qw-client-bootstrap"}})
	if err == nil || !strings.Contains(err.Error(), "revise text_replacements") {
		t.Fatalf("update explicito do bootstrap deveria pedir revisao: %v\n%s", err, out.String())
	}
}

func TestEveryNQuakeFileIsClassified(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	web := currentUpstreams(t, root, "")
	web.nquake.files["sv-maps/qw/maps/novo.bsp"] = NQuakeFile{3, sha256Hex([]byte("bsp")), gitBlob([]byte("bsp"))}
	web.nquake.files["sv-novo/leia.txt"] = NQuakeFile{4, sha256Hex([]byte("novo")), gitBlob([]byte("novo"))}
	project := NewProject(root)
	project.HTTP = web.client()
	var out, info bytes.Buffer
	code, err := project.Check(&out, &info, false, false, nil)
	if err != nil || code != 2 {
		t.Fatalf("check com arquivos novos no nQuake: %d %v\n%s", code, err, out.String())
	}
	if !strings.Contains(out.String(), "[NQUAKE] nao usado pela distro, sv-maps: 1 novo(s), 0 alterado(s), 0 removido(s)") ||
		!strings.Contains(out.String(), "[CLASSIFICAR] sv-novo/leia.txt: ") {
		t.Fatalf("check deveria classificar as mudancas do nQuake:\n%s", out.String())
	}

	// Unclassified files keep the inventory where it is; the rest of the
	// update goes on.
	update := NewProject(root)
	update.HTTP = web.client()
	out.Reset()
	if err := update.Update(&out, UpdateOptions{Workers: 4, DryRun: true}); err != nil ||
		!strings.Contains(out.String(), "[CLASSIFICAR] sv-novo/leia.txt: ") || strings.Contains(out.String(), "[INFO] Inventario nQuake") {
		t.Fatalf("update nao deveria avancar o inventario com arquivo nao classificado: %v\n%s", err, out.String())
	}
}

func TestSymbolicLinksInTheNQuakeTreeAreIgnored(t *testing.T) {
	root := repoRoot(t)
	requireHydrated(t, root)
	web := currentUpstreams(t, root, "")
	upstream := web.handler
	web.handler = func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, "/git/trees/") {
			upstream(w, r)
			return
		}
		recorded := httptest.NewRecorder()
		upstream(recorded, r)
		listing, _ := pyjson.Decode(recorded.Body.Bytes(), pyjson.RejectDuplicates)
		entries := append(pyjson.Get(listing, "tree").([]any), object("path", "CLAUDE.md", "mode", "120000", "type", "blob",
			"sha", "5555555555555555555555555555555555555555", "size", 9))
		listing.(*Object).Set("tree", entries)
		writeJSONResponse(w, listing)
	}
	project := NewProject(root)
	project.HTTP = web.client()
	var out, info bytes.Buffer
	code, err := project.Check(&out, &info, false, false, []string{"nquake-maps"})
	if err != nil || code != 0 || !strings.Contains(out.String(), "[OK] Conteudo baseado no nQuake") && !strings.Contains(out.String(), "nquake-maps") {
		t.Fatalf("symlink na arvore nQuake deveria ser ignorado: %d %v\n%s", code, err, out.String())
	}
	if strings.Contains(out.String(), "INDISPONIVEL") {
		t.Fatalf("symlink tornou o nQuake indisponivel:\n%s", out.String())
	}
}

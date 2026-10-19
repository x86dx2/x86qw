package content

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"golang.org/x/text/unicode/norm"
)

// manage.py add: reviewed distribution-change definitions.

var (
	safeIdentifier = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]*$`)
	safeConsumer   = regexp.MustCompile(`^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9_.+-]*$`)
)

// safeRelative mirrors manage.safe_relative.
func safeRelative(value any, prefixDir string) (string, error) {
	portable, err := ValidatePortableRelativePath(value, "caminho")
	if err != nil {
		return "", &ManagerError{err.Error()}
	}
	parts := strings.Split(portable, "/")
	if len(parts) < 2 {
		return "", managerError("caminho inseguro: %s", pyjson.Repr(value))
	}
	if parts[0] != prefixDir {
		return "", managerError("o caminho precisa ficar em %s/: %s", prefixDir, pyjson.Repr(value))
	}
	return portable, nil
}

func semanticIdentity(value string) string { return Casefold(norm.NFC.String(value)) }

// validateDestinationTree mirrors manage.validate_destination_tree.
func (p *Project) validateDestinationTree(destination string) error {
	relative := strings.Split(strings.TrimPrefix(destination, "dist/"), "/")
	existing := map[string]string{}
	dist := p.Path(DistDir)
	filepath.WalkDir(dist, func(path string, entry os.DirEntry, err error) error {
		if err != nil || path == dist {
			return nil
		}
		name, _ := filepath.Rel(dist, path)
		name = filepath.ToSlash(name)
		if _, ok := existing[semanticIdentity(name)]; !ok {
			existing[semanticIdentity(name)] = name
		}
		return nil
	})
	for depth := 1; depth <= len(relative); depth++ {
		candidate := strings.Join(relative[:depth], "/")
		if known, ok := existing[semanticIdentity(candidate)]; ok && known != candidate {
			return managerError("destino colide com caminho existente em caixa ou Unicode: %s", candidate)
		}
		target := joinPosix(dist, candidate)
		if isSymlink(target) {
			return managerError("destino atravessa symlink existente: %s", candidate)
		}
		if exists(target) {
			if depth < len(relative) && !isDir(target) {
				return managerError("pai do destino nao e diretorio: %s", candidate)
			}
			if depth == len(relative) && !isFile(target) {
				return managerError("destino existente nao e arquivo regular: %s", candidate)
			}
		}
	}
	return nil
}

// validateLocalSourcePath mirrors manage.validate_local_source_path.
func validateLocalSourcePath(base string, value any) (string, error) {
	portable, err := ValidatePortableRelativePath(value, "source local")
	if err != nil {
		return "", &ManagerError{err.Error()}
	}
	root, err := filepath.EvalSymlinks(base)
	if err != nil {
		return "", err
	}
	candidate := root
	for _, part := range strings.Split(portable, "/") {
		candidate = filepath.Join(candidate, part)
		if isSymlink(candidate) {
			return "", managerError("source local precisa ser arquivo regular sem symlink")
		}
	}
	if !isFile(candidate) {
		return "", managerError("source local precisa ser arquivo regular sem symlink")
	}
	if relative, err := filepath.Rel(root, resolve(candidate)); err != nil || strings.HasPrefix(relative, "..") {
		return "", managerError("source local escapa do diretorio da definicao")
	}
	return candidate, nil
}

type filePlan struct {
	entry       *Object
	destination string
	source      string
	url         string
	origin      string
	size        int64
	sha256      string
	mode        os.FileMode
}

// validateDefinitionFile mirrors manage.validate_definition_file.
func validateDefinitionFile(raw any, base string) (*filePlan, error) {
	entry, ok := raw.(*Object)
	if !ok {
		return nil, managerError("entrada de arquivo invalida")
	}
	destination, err := safeRelative(entry.Value("destination"), "dist")
	if err != nil {
		return nil, err
	}
	source, url := entry.Value("source"), entry.Value("url")
	if (source == nil) == (url == nil) {
		return nil, managerError("cada arquivo da definicao deve possuir exatamente source ou url")
	}
	plan := &filePlan{entry: entry, destination: destination}
	var parsedURL splitURL
	if source != nil {
		origin, err := validateLocalSourcePath(base, source)
		if err != nil {
			return nil, err
		}
		expectedSize, expectedSHA := entry.Value("size"), entry.Value("sha256")
		if expectedSize != nil && !sizeWithin(expectedSize, true) {
			return nil, managerError("size local invalido na definicao")
		}
		if text, ok := expectedSHA.(string); expectedSHA != nil && (!ok || !sha256Pattern.MatchString(text)) {
			return nil, managerError("sha256 local invalido na definicao")
		}
		info, err := os.Stat(origin)
		if err != nil {
			return nil, err
		}
		if info.Size() <= 0 || info.Size() > MaxArtifactBytes {
			return nil, managerError("arquivo local precisa ser nao vazio e respeitar o limite global")
		}
		if expectedSize != nil && !pyjson.EqInt(expectedSize, info.Size()) {
			return nil, managerError("size local nao corresponde ao arquivo da definicao")
		}
		digest, err := fileSHA256(origin)
		if err != nil {
			return nil, err
		}
		if expectedSHA != nil && expectedSHA != digest {
			return nil, managerError("sha256 local nao corresponde ao arquivo da definicao")
		}
		plan.source, plan.origin, plan.size, plan.sha256 = origin, "arquivo local validado", info.Size(), digest
		plan.mode = 0o644
		if info.Mode()&0o111 != 0 {
			plan.mode = 0o755
		}
	} else {
		text, ok := url.(string)
		if !ok {
			return nil, managerError("URL de arquivo invalida")
		}
		if parsedURL, err = validateHTTPSURL(text, "URL do arquivo da definicao"); err != nil {
			return nil, &ManagerError{err.Error()}
		}
		size, digest := entry.Value("size"), entry.Value("sha256")
		digestText, digestOK := digest.(string)
		if !sizeWithin(size, true) || !digestOK || !sha256Pattern.MatchString(digestText) {
			return nil, managerError("arquivo remoto da definição exige size e sha256 válidos")
		}
		plan.url, plan.origin, plan.size, plan.sha256 = text, safeURLForLog(text), int64Of(size), digestText
	}
	managed := getOr(entry, "managed", false)
	if _, ok := managed.(bool); !ok {
		return nil, managerError("managed precisa ser booleano")
	}
	if managed == true {
		if source != nil {
			return nil, managerError("arquivo upstream gerenciado precisa usar URL HTTPS fixada")
		}
		if parsedURL.Query != "" {
			return nil, managerError("URL persistente de arquivo gerenciado nao pode conter query")
		}
		component, componentOK := entry.Value("distribution_component").(string)
		consumer, consumerOK := entry.Value("consumer").(string)
		if !componentOK || !componentID.MatchString(component) || !consumerOK || !safeConsumer.MatchString(consumer) {
			return nil, managerError("arquivo upstream gerenciado exige distribution_component e consumer seguros")
		}
		if pkg := entry.Value("package"); pkg != nil {
			if text, ok := pkg.(string); !ok || !safeIdentifier.MatchString(text) {
				return nil, managerError("package do arquivo gerenciado precisa ser identificador seguro")
			}
		}
	}
	return plan, nil
}

// applyReferenceTransition mirrors manage.apply_reference_transition.
func applyReferenceTransition(releases *Object, proposed any) ([2]string, bool, error) {
	if proposed == nil {
		return [2]string{}, false, nil
	}
	transition, ok := proposed.(*Object)
	if !ok || !sameKeys(transition.Keys(), "repository", "previous_revision", "revision") {
		return [2]string{}, false, managerError("reference precisa declarar somente repository, previous_revision e revision")
	}
	current, ok := releases.Value("reference").(*Object)
	if !ok {
		return [2]string{}, false, managerError("inventario de releases nao possui referencia nQuake")
	}
	repository, repositoryOK := transition.Value("repository").(string)
	previous, previousOK := transition.Value("previous_revision").(string)
	revision, revisionOK := transition.Value("revision").(string)
	if !repositoryOK || !previousOK || !hex40Strict.MatchString(previous) || !revisionOK || !hex40Strict.MatchString(revision) {
		return [2]string{}, false, managerError("transicao da referencia nQuake possui campos invalidos")
	}
	parsed, err := validateHTTPSURL(repository, "repository da referencia nQuake")
	if err != nil {
		return [2]string{}, false, managerError("repository da referencia nQuake invalido")
	}
	if parsed.Query != "" {
		return [2]string{}, false, managerError("repository da referencia nQuake nao pode conter query")
	}
	if !pyjson.Eq(repository, current.Value("repository")) {
		return [2]string{}, false, managerError("transicao da referencia nQuake nao pode trocar o repository")
	}
	if !pyjson.Eq(previous, current.Value("revision")) {
		return [2]string{}, false, managerError("previous_revision da referencia nQuake diverge do inventario atual")
	}
	if revision == previous {
		return [2]string{}, false, managerError("transicao da referencia nQuake precisa alterar a revisao")
	}
	if _, err := UpdateReferenceReleases(releases, revision); err != nil {
		return [2]string{}, false, err
	}
	return [2]string{previous, revision}, true, nil
}

// referenceAssetURL mirrors manage.reference_asset_url.
func referenceAssetURL(repository, revision, upstreamPath string) (string, error) {
	parsed, err := validateHTTPSURL(repository, "repository da referencia nQuake")
	if err != nil {
		return "", managerError("repository da referencia nQuake invalido")
	}
	parts := strings.Split(strings.Trim(parsed.Path, "/"), "/")
	_, _, _, port, hasPort := parsed.hostinfo()
	if Casefold(parsed.Hostname()) != "github.com" || (hasPort && port != "443") || parsed.Query != "" ||
		len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return "", managerError("repository da referencia nQuake nao e um repositorio GitHub canonico")
	}
	return "https://raw.githubusercontent.com/" + parts[0] + "/" + parts[1] + "/" + revision + "/" + quotePath(upstreamPath), nil
}

// upsertComponent mirrors manage.upsert_component.
func upsertComponent(catalog *Object, component *Object, replace bool) error {
	entries, ok := catalog.Value("components").([]any)
	identifier, idOK := component.Value("id").(string)
	if !ok || !idOK {
		return managerError("componente da definicao invalido")
	}
	index := -1
	for position, item := range objects(entries) {
		if item.Value("id") == identifier && index < 0 {
			index = position
		}
	}
	if index >= 0 && !replace {
		return managerError("o componente %s ja existe; declare replace: true para atualiza-lo", identifier)
	}
	if index >= 0 {
		for position, raw := range entries {
			if item, ok := raw.(*Object); ok && item.Value("id") == identifier {
				entries[position] = component
				break
			}
		}
	} else {
		entries = append(entries, component)
	}
	sort.SliceStable(entries, func(i, j int) bool {
		return pyjson.Repr(getOr(entries[i].(*Object), "id", "")) < pyjson.Repr(getOr(entries[j].(*Object), "id", ""))
	})
	catalog.Set("components", entries)
	return nil
}

// preserveProfileFingerprints mirrors manage.preserve_profile_fingerprints.
func preserveProfileFingerprints(catalog *Object) error {
	profiles, profilesOK := catalog.Value("profiles").(*Object)
	history, historyOK := catalog.Value("profile_history").(*Object)
	if !profilesOK || !historyOK || !sameKeys(profiles.Keys(), history.Keys()...) {
		return managerError("historico de perfis de componentes invalido")
	}
	for _, name := range profiles.Keys() {
		selected, selectedOK := pyjson.StrList(profiles.Value(name))
		fingerprints, fingerprintsOK := history.Value(name).([]any)
		if !selectedOK || !fingerprintsOK {
			return managerError("historico do perfil %s invalido", name)
		}
		fingerprint := ProfileFingerprint(selected)
		if !listContains(fingerprints, fingerprint) {
			history.Set(name, append(fingerprints, fingerprint))
		}
	}
	return nil
}

func addToProfiles(staged *Object, identifier string, profiles []string) error {
	profileMap, ok := staged.Value("profiles").(*Object)
	if !ok {
		return managerError("perfis de componentes invalidos")
	}
	for _, profile := range profiles {
		members, ok := profileMap.Value(profile).([]any)
		if !ok {
			return managerError("perfil desconhecido: %s", profile)
		}
		if !listContains(members, identifier) {
			profileMap.Set(profile, append(members, identifier))
		}
	}
	return nil
}

// validateDistributionChange mirrors manage.validate_distribution_change.
func (p *Project) validateDistributionChange(definition *Object, definitionPath string) ([]*filePlan, error) {
	if !pyjson.EqInt(definition.Value("format"), 1) || !pyjson.Eq(definition.Value("project"), "x86qw") || !pyjson.Eq(definition.Value("kind"), "distribution-change") {
		return nil, managerError("a definicao precisa ser x86qw/distribution-change formato 1")
	}
	files, ok := getOr(definition, "files", []any{}).([]any)
	if !ok || len(files) == 0 {
		return nil, managerError("a definicao precisa incorporar ao menos um arquivo diretamente utilizado")
	}
	var plans []*filePlan
	for _, raw := range files {
		plan, err := validateDefinitionFile(raw, filepath.Dir(definitionPath))
		if err != nil {
			return nil, err
		}
		plans = append(plans, plan)
	}
	identities := map[string]bool{}
	for _, plan := range plans {
		identities[semanticIdentity(plan.destination)] = true
	}
	if len(identities) != len(plans) {
		return nil, managerError("a definicao possui destinos duplicados ou colidentes")
	}
	for _, plan := range plans {
		if err := p.validateDestinationTree(plan.destination); err != nil {
			return nil, err
		}
	}
	policy, _, err := p.Policy()
	if err != nil {
		return nil, managerError("inventario da distribuicao invalido: %v", err)
	}
	manifest, err := LoadManifest(p.Path(ManifestPath))
	if err != nil {
		return nil, managerError("inventario da distribuicao invalido: %v", err)
	}
	registry, err := p.Upstreams()
	if err != nil {
		return nil, managerError("inventario da distribuicao invalido: %v", err)
	}
	currentFiles := manifest.Value("files").(*Object)
	currentCatalog, err := LoadJSONObject(p.Path(CatalogPath))
	if err != nil {
		return nil, err
	}
	if _, err := p.ValidateCatalog(currentCatalog); err != nil {
		return nil, managerError("catalogo publico atual invalido: %v", err)
	}
	pkgValue := definition.Value("package")
	pkg, _ := pkgValue.(*Object)
	if pkgValue != nil {
		if _, err := p.ValidatePackage(pkgValue, "definition.package", true); err != nil {
			return nil, &ManagerError{err.Error()}
		}
		if pkg.Value("component") == "installer" {
			return nil, managerError("pacotes do instalador so podem ser preparados pelo fluxo de release imutavel")
		}
	}
	stagedComponents, err := LoadJSONObject(p.Path(ComponentsPath))
	if err != nil {
		return nil, err
	}
	stagedReleases, err := LoadJSONObject(p.Path(ReleasesPath))
	if err != nil {
		return nil, err
	}
	transition, hasTransition, err := applyReferenceTransition(stagedReleases, definition.Value("reference"))
	if err != nil {
		return nil, err
	}
	replace := getOr(definition, "replace", false)
	if _, ok := replace.(bool); !ok {
		return nil, managerError("replace precisa ser booleano")
	}
	if componentValue := definition.Value("component"); componentValue != nil {
		component, ok := componentValue.(*Object)
		if !ok {
			return nil, managerError("component precisa ser um objeto")
		}
		if identifier, ok := component.Value("id").(string); !ok || !componentID.MatchString(identifier) {
			return nil, managerError("component.id precisa ser identificador seguro")
		}
		profiles, ok := pyjson.StrList(getOr(definition, "profiles", []any{}))
		for _, profile := range profiles {
			ok = ok && componentID.MatchString(profile)
		}
		if !ok {
			return nil, managerError("profiles precisa ser uma lista de identificadores seguros")
		}
		proposed, ok := definition.Value("release").(*Object)
		if !ok {
			return nil, managerError("todo componente novo ou substituido precisa declarar release")
		}
		entries, ok := stagedReleases.Value("components").(*Object)
		if !ok {
			return nil, managerError("inventario de releases nao possui componentes validos")
		}
		identifier := component.Value("id").(string)
		if current, ok := entries.Value(identifier).(*Object); replace == true && ok {
			currentNamespace := getOr(current, "distribution_component", "nquake")
			if !pyjson.Eq(getOr(proposed, "distribution_component", "nquake"), currentNamespace) {
				return nil, managerError("release substituta precisa preservar distribution_component %s: %s", pyjson.Repr(currentNamespace), identifier)
			}
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return nil, err
		}
		if err := upsertComponent(stagedComponents, component, replace == true); err != nil {
			return nil, err
		}
		if err := addToProfiles(stagedComponents, identifier, profiles); err != nil {
			return nil, err
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return nil, err
		}
		entries.Set(identifier, proposed)
	} else if definition.Has("profiles") || definition.Has("release") {
		return nil, managerError("profiles e release exigem component")
	}
	if err := ValidateComponentCatalog(stagedComponents); err != nil {
		return nil, managerError("inventarios propostos invalidos: %v", err)
	}
	namespaces, _ := pyjson.StrList(stagedComponents.Value("content_namespaces"))
	if err := ValidateReleases(stagedReleases, setOf(ComponentIDs(stagedComponents)...), setOf(namespaces...)); err != nil {
		return nil, managerError("inventarios propostos invalidos: %v", err)
	}
	projectConsumers := func(path string) int {
		consumers := map[string]bool{}
		for _, candidate := range objects(stagedComponents.Value("components")) {
			identifier, ok := candidate.Value("id").(string)
			if !ok {
				continue
			}
			for _, field := range []string{"project_sources", "project_inputs", "project_overrides", "project_archive_overrides"} {
				for _, item := range objects(getOr(candidate, field, []any{})) {
					if item.Value("path") == path {
						consumers[identifier] = true
					}
				}
			}
		}
		return len(consumers)
	}
	type authority struct {
		releaseID, owner string
		artifact         *Object
	}
	stagedAuthority := func(path string) (*authority, error) {
		entries, ok := stagedReleases.Value("components").(*Object)
		if !ok {
			return nil, nil
		}
		var matches []authority
		for _, releaseID := range entries.Keys() {
			release, ok := entries.Value(releaseID).(*Object)
			if !ok {
				continue
			}
			for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
				if artifact.Value("distribution_path") == path {
					matches = append(matches, authority{releaseID, pyjson.Repr(getOr(release, "distribution_component", "nquake")), artifact})
				}
			}
		}
		if len(matches) > 1 {
			return nil, managerError("destino aparece em mais de uma release proposta: %s", path)
		}
		if len(matches) == 0 {
			return nil, nil
		}
		return &matches[0], nil
	}
	requireIdentity := func(plan *filePlan, record *Object, label, urlField string) error {
		if !pyjson.Eq(plan.entry.Value("url"), record.Value(urlField)) || !pyjson.EqInt(record.Value("size"), plan.size) || !pyjson.Eq(record.Value("sha256"), plan.sha256) {
			return managerError("URL, tamanho ou SHA-256 diverge do %s: %s", label, plan.destination)
		}
		return nil
	}
	preservedSource := func(path string) (*Object, error) {
		var matches []*Object
		for _, upstream := range objects(registry.Value("upstreams")) {
			if source, ok := upstream.Value("source").(*Object); ok && source.Value("distribution_path") == path {
				matches = append(matches, source)
			}
		}
		if len(matches) > 1 {
			return nil, managerError("fonte preservada duplicada no inventario: %s", path)
		}
		if len(matches) == 0 {
			return nil, nil
		}
		return matches[0], nil
	}
	stagedProject := p.Staged(p.Path(ComponentsPath), p.Path(ReleasesPath))
	for _, plan := range plans {
		entry := plan.entry
		relative := strings.TrimPrefix(plan.destination, "dist/")
		if plan.source != "" {
			if currentFiles.Has(relative) {
				return nil, managerError("fonte local nao pode substituir arquivo upstream gerenciado: %s", relative)
			}
			if projectConsumers(plan.destination) != 1 {
				return nil, managerError("fonte local precisa de um consumidor exato no BOM proposto: %s", plan.destination)
			}
			continue
		}
		if entry.Value("managed") != true {
			return nil, managerError("todo arquivo remoto persistente precisa declarar managed: true")
		}
		componentName := entry.Value("distribution_component").(string)
		consumer := entry.Value("consumer").(string)
		if err := p.RequireComponent(componentName, &relative); err != nil {
			return nil, &ManagerError{err.Error()}
		}
		owner, err := p.ComponentForDistributionPath(relative)
		if err != nil {
			return nil, &ManagerError{err.Error()}
		}
		if owner != componentName {
			return nil, managerError("componente declarado nao e dono do destino: %s != %s", componentName, ownerRepr(owner))
		}
		existing, _ := currentFiles.Value(relative).(*Object)
		sourceComponent := SourceOwner(registry, relative)
		allowed := setOf(objectsOrStrings(policy[componentName].Value("consumers"))...)
		if sourceComponent != "" {
			allowed["development:"+sourceComponent] = true
		}
		if existing != nil && existing.Value("component") == componentName {
			if text, ok := existing.Value("consumer").(string); ok {
				allowed[text] = true
			}
		}
		if !allowed[consumer] {
			return nil, managerError("consumer nao declarado para %s: %s", componentName, consumer)
		}
		referencePackage := ""
		if componentName == "nquake" {
			parts := purePosix(relative).parts
			if len(parts) >= 4 && parts[0] == "distributions" && parts[1] == "nquake" {
				reference, ok := stagedReleases.Value("reference").(*Object)
				if !ok {
					return nil, managerError("inventario de releases nao possui referencia nQuake")
				}
				revision, ok := reference.Value("revision").(string)
				if !ok || !hex40Strict.MatchString(revision) || parts[2] != revision {
					return nil, managerError("revisao do snapshot nQuake diverge da referencia fixada: %s", parts[2])
				}
				upstreamPath := strings.Join(parts[3:], "/")
				repository, ok := reference.Value("repository").(string)
				if !ok {
					return nil, managerError("inventario de releases nao possui repository nQuake")
				}
				expected, err := referenceAssetURL(repository, revision, upstreamPath)
				if err != nil {
					return nil, err
				}
				if !pyjson.Eq(entry.Value("url"), expected) {
					return nil, managerError("URL do snapshot nQuake diverge da referencia fixada: %s", relative)
				}
				if referencePackage, err = ComponentForSource(stagedComponents, upstreamPath, "reference"); err != nil {
					return nil, err
				}
			}
		}
		artifactAuthority, err := stagedAuthority(relative)
		if err != nil {
			return nil, err
		}
		if artifactAuthority != nil && artifactAuthority.owner != componentName {
			return nil, managerError("distribution_component da release diverge do plano gerenciado: %s != %s: %s", artifactAuthority.owner, componentName, relative)
		}
		var expectedPackage any
		switch {
		case referencePackage != "":
			expectedPackage = referencePackage
		case artifactAuthority != nil:
			expectedPackage = artifactAuthority.releaseID
		case sourceComponent != "":
			expectedPackage = sourceComponent
		case existing != nil:
			current := existing.Value("package")
			if _, ok := current.(string); current != nil && !ok {
				return nil, managerError("package atual invalido no manifesto: %s", relative)
			}
			expectedPackage = current
		}
		currentOwner, err := stagedProject.ConsumedComponent(relative)
		if err != nil {
			return nil, managerError("destino gerenciado invalido: %v", err)
		}
		stagedOwner := ""
		if artifactAuthority != nil {
			stagedOwner = artifactAuthority.owner
		}
		if currentOwner != componentName && stagedOwner != componentName &&
			!(componentName == "nquake" && referencePackage != "" && sourceComponent == "") {
			return nil, managerError("destino nao possui consumidor operacional declarado: %s", relative)
		}
		provided := entry.Value("package")
		if expectedPackage == nil && provided != nil {
			return nil, managerError("package nao pertence ao destino gerenciado: %s", relative)
		}
		if expectedPackage != nil && !pyjson.Eq(provided, expectedPackage) {
			return nil, managerError("package do arquivo gerenciado deve ser %s: %s", pyjson.Repr(expectedPackage), relative)
		}
		if existing != nil {
			if err := requireIdentity(plan, existing, "manifesto imutavel atual", "url"); err != nil {
				return nil, err
			}
			for _, field := range [][2]string{{"component", "distribution_component"}, {"consumer", "consumer"}, {"package", "package"}} {
				if !pyjson.Eq(entry.Value(field[1]), existing.Value(field[0])) {
					return nil, managerError("metadado %s diverge do manifesto imutavel atual: %s", field[0], relative)
				}
			}
			continue
		}
		if strings.HasPrefix(relative, "installer/") {
			return nil, managerError("novos pacotes do instalador exigem o fluxo de release imutavel")
		}
		authorities := 0
		if artifactAuthority != nil {
			if !pyjson.Eq(entry.Value("package"), artifactAuthority.releaseID) {
				return nil, managerError("package do arquivo gerenciado diverge da release proposta: %s", relative)
			}
			if err := requireIdentity(plan, artifactAuthority.artifact, "release proposta", "url"); err != nil {
				return nil, err
			}
			authorities++
		}
		record, err := preservedSource(relative)
		if err != nil {
			return nil, err
		}
		if record != nil {
			if err := requireIdentity(plan, record, "registro de upstream", "url"); err != nil {
				return nil, err
			}
			authorities++
		}
		if pkg != nil && pkg.Value("distribution_path") == relative {
			if err := requireIdentity(plan, pkg, "pacote publico proposto", "origin_url"); err != nil {
				return nil, err
			}
			authorities++
		}
		if componentName == "nquake" && referencePackage != "" {
			authorities++
		}
		if authorities == 0 {
			return nil, managerError("arquivo remoto nao esta vinculado a release, upstream ou pacote proposto: %s", relative)
		}
	}
	if hasTransition {
		prefix := "dist/distributions/nquake/" + transition[1] + "/"
		found := false
		for _, plan := range plans {
			found = found || strings.HasPrefix(plan.destination, prefix)
		}
		if !found {
			return nil, managerError("transicao da referencia nQuake precisa incorporar o novo snapshot")
		}
	}
	if pkg != nil {
		distributionPath, isText := pkg.Value("distribution_path").(string)
		if isText && currentFiles.Has(distributionPath) {
			return nil, managerError("um pacote proposto nao pode reutilizar caminho ja registrado no manifesto")
		}
		var matching *filePlan
		for _, plan := range plans {
			if isText && plan.destination == "dist/"+distributionPath {
				matching = plan
				break
			}
		}
		if matching == nil {
			return nil, managerError("o pacote declarado precisa corresponder a um arquivo da definicao")
		}
		if !pyjson.EqInt(pkg.Value("size"), matching.size) || !pyjson.Eq(pkg.Value("sha256"), matching.sha256) {
			return nil, managerError("o pacote declarado diverge dos pins do arquivo da definicao")
		}
		if !pyjson.Eq(matching.entry.Value("url"), pkg.Value("origin_url")) {
			return nil, managerError("a origem do pacote diverge da URL do arquivo da definicao")
		}
		if matching.entry.Value("managed") != true {
			return nil, managerError("o pacote declarado precisa corresponder a arquivo gerenciado")
		}
		if !pyjson.Eq(matching.entry.Value("distribution_component"), pkg.Value("component")) {
			return nil, managerError("componente do pacote diverge do dono do arquivo gerenciado")
		}
		expectedPublic := matching.entry.Value("package")
		if !pyjson.Truthy(expectedPublic) {
			expectedPublic = matching.entry.Value("distribution_component")
		}
		public := getOr(pkg, "package", pkg.Value("component"))
		if !pyjson.Eq(public, expectedPublic) {
			return nil, managerError("identidade logica do pacote publico diverge do arquivo gerenciado: %s != %s", pyjson.Repr(public), pyjson.Repr(expectedPublic))
		}
		entries, ok := currentCatalog.Value("packages").([]any)
		if !ok {
			return nil, managerError("catalogo publico atual nao possui pacotes validos")
		}
		if packageIdentityExists(entries, pkg) {
			return nil, managerError("a identidade de pacote ja existe: %s", identityRepr(pkg))
		}
		proposed := currentCatalog.Clone()
		proposed.Set("packages", append(append([]any{}, entries...), pkg))
		if _, err := p.ValidateCatalog(proposed); err != nil {
			return nil, managerError("catalogo publico proposto invalido: %v", err)
		}
	}
	return plans, nil
}

func ownerRepr(owner string) string {
	if owner == "" {
		return "None"
	}
	return owner
}

var identityFields = []string{"component", "version", "channel", "platform", "architecture"}

func packageIdentityExists(entries []any, pkg *Object) bool {
	for _, item := range objects(entries) {
		same := true
		for _, field := range identityFields {
			same = same && pyjson.Eq(item.Value(field), pkg.Value(field))
		}
		if same {
			return true
		}
	}
	return false
}

func identityRepr(pkg *Object) string {
	var parts []string
	for _, field := range identityFields {
		parts = append(parts, pyRepr(pkg.Value(field)))
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

// fetchDefinitionFile mirrors manage.fetch_definition_file.
func (p *Project) fetchDefinitionFile(plan *filePlan, destination string) (int64, string, error) {
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return 0, "", err
	}
	if plan.source != "" {
		payload, err := os.ReadFile(plan.source)
		if err != nil {
			return 0, "", managerError("nao foi possivel copiar o source local: %v", err)
		}
		if int64(len(payload)) != plan.size || sha256Hex(payload) != plan.sha256 {
			return 0, "", managerError("source local mudou depois da validacao")
		}
		os.Remove(destination) // never write through a workspace hard link
		if err := writeAtomic(destination, payload, plan.mode); err != nil {
			return 0, "", err
		}
	} else if err := p.Pinned(plan.url, destination, plan.size, plan.sha256, 1); err != nil {
		return 0, "", managerError("falha ao baixar arquivo da definição: %v", err)
	}
	size, err := fileSize(destination)
	if err != nil {
		return 0, "", err
	}
	digest, err := fileSHA256(destination)
	if err != nil {
		return 0, "", err
	}
	label := pyjson.Repr(plan.entry.Value("destination"))
	if size != plan.size {
		return 0, "", managerError("tamanho inesperado para %s", label)
	}
	if digest != plan.sha256 {
		return 0, "", managerError("SHA-256 inesperado para %s", label)
	}
	return size, digest, nil
}

// AddOptions mirrors manage.py add flags.
type AddOptions struct {
	DryRun  bool
	Yes     bool
	Confirm Confirm
}

// Add mirrors manage.py add.
func (p *Project) Add(out io.Writer, definitionPath string, options AddOptions) error {
	definitionPath, err := filepath.Abs(definitionPath)
	if err != nil {
		return err
	}
	definition, err := LoadJSONObject(definitionPath)
	if err != nil {
		return err
	}
	plans, err := p.validateDistributionChange(definition, definitionPath)
	if err != nil {
		return err
	}
	files := definition.Value("files").([]any)
	if options.DryRun {
		fmt.Fprintf(out, "[PLANO] Definicao valida: %d arquivo(s).\n", len(plans))
		for _, plan := range plans {
			fmt.Fprintf(out, "  %s | %s\n", plan.destination, plan.origin)
		}
		fmt.Fprintln(out, "[OK] Simulacao concluida; nenhum byte remoto foi baixado e nenhum arquivo foi alterado.")
		return nil
	}
	if err := p.requireCleanWorktree(); err != nil {
		return err
	}
	if !options.Yes {
		if err := options.Confirm(fmt.Sprintf("Incorporar %d arquivo(s) e atualizar os inventarios?", len(files))); err != nil {
			return err
		}
	}
	work, err := p.prepareWorkspace()
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)
	stagedComponents, err := LoadJSONObject(filepath.Join(work, "components.json"))
	if err != nil {
		return err
	}
	stagedReleases, err := LoadJSONObject(filepath.Join(work, "component-releases.json"))
	if err != nil {
		return err
	}
	stagedCatalog, err := LoadJSONObject(filepath.Join(work, "catalog.json"))
	if err != nil {
		return err
	}
	transition, hasTransition, err := applyReferenceTransition(stagedReleases, definition.Value("reference"))
	if err != nil {
		return err
	}
	if componentValue := definition.Value("component"); componentValue != nil {
		component, ok := componentValue.(*Object)
		if !ok {
			return managerError("component precisa ser um objeto")
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return err
		}
		if err := upsertComponent(stagedComponents, component, pyjson.Truthy(definition.Value("replace"))); err != nil {
			return err
		}
		identifier := pyjson.Repr(component.Value("id"))
		profiles, ok := pyjson.StrList(getOr(definition, "profiles", []any{}))
		if !ok {
			return managerError("profiles precisa ser uma lista")
		}
		if err := addToProfiles(stagedComponents, identifier, profiles); err != nil {
			return err
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return err
		}
		release, releaseOK := definition.Value("release").(*Object)
		entries, entriesOK := stagedReleases.Value("components").(*Object)
		if !releaseOK || !entriesOK {
			return managerError("todo componente novo ou substituido precisa declarar release")
		}
		entries.Set(identifier, release)
	}
	manifestPath := filepath.Join(work, "dist", "manifest.json")
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return err
	}
	manifestFiles := manifest.Value("files").(*Object)
	if hasTransition {
		previousSnapshot := filepath.Join(work, "dist", "distributions", "nquake", transition[0])
		if !isDir(previousSnapshot) || isSymlink(previousSnapshot) {
			return managerError("snapshot nQuake anterior ausente ou inseguro no workspace")
		}
		os.RemoveAll(previousSnapshot)
		for _, relative := range manifestFiles.Keys() {
			if strings.HasPrefix(relative, "distributions/nquake/"+transition[0]+"/") {
				manifestFiles.Delete(relative)
			}
		}
	}
	for index, raw := range files {
		entry, ok := raw.(*Object)
		if !ok {
			return managerError("entrada de arquivo invalida")
		}
		destination, err := safeRelative(entry.Value("destination"), "dist")
		if err != nil {
			return err
		}
		relative := strings.TrimPrefix(destination, "dist/")
		size, digest, err := p.fetchDefinitionFile(plans[index], joinPosix(filepath.Join(work, "dist"), relative))
		if err != nil {
			return err
		}
		if entry.Value("managed") == true {
			component, consumer, url := entry.Value("distribution_component"), entry.Value("consumer"), entry.Value("url")
			if !pyjson.NonEmptyStr(component) || !pyjson.NonEmptyStr(consumer) || !pyjson.NonEmptyStr(url) {
				return managerError("arquivo upstream gerenciado exige distribution_component, consumer e url")
			}
			metadata := object("component", component, "consumer", consumer, "url", url,
				"size", pyjson.Number(fmt.Sprint(size)), "sha256", digest)
			if text, ok := entry.Value("package").(string); ok {
				metadata.Set("package", text)
			}
			manifestFiles.Set(relative, metadata)
		}
	}
	if err := WriteManifest(manifestPath, manifest); err != nil {
		return err
	}
	if pkgValue := definition.Value("package"); pkgValue != nil {
		if _, err := p.ValidatePackage(pkgValue, "definition.package", true); err != nil {
			return err
		}
		pkg := pkgValue.(*Object)
		artifact := joinPosix(filepath.Join(work, "dist"), pyjson.Repr(getOr(pkg, "distribution_path", "")))
		size, _ := fileSize(artifact)
		digest, _ := fileSHA256(artifact)
		if !isFile(artifact) || !pyjson.EqInt(pkg.Value("size"), size) || !pyjson.Eq(pkg.Value("sha256"), digest) {
			return managerError("o pacote declarado nao corresponde ao arquivo incorporado")
		}
		entries, ok := stagedCatalog.Value("packages").([]any)
		if !ok {
			return managerError("catalogo de pacotes invalido")
		}
		if packageIdentityExists(entries, pkg) {
			return managerError("a identidade de pacote ja existe: %s", identityRepr(pkg))
		}
		stagedCatalog.Set("packages", append(entries, pkg))
		stagedCatalog.Set("generated_at", utcNow())
	}
	for name, document := range map[string]*Object{"components.json": stagedComponents, "component-releases.json": stagedReleases, "catalog.json": stagedCatalog} {
		if err := WriteJSON(filepath.Join(work, name), document); err != nil {
			return err
		}
	}
	staged, err := p.validateStaged(work)
	if err != nil {
		return err
	}
	manifestBuild, err := staged.BuildPackages(filepath.Join(work, "dist"), filepath.Join(work, "packages"), BuildOptions{})
	if err != nil {
		return err
	}
	if err := p.RegisterPackages(filepath.Join(work, "catalog.json"), manifestBuild, time.Now()); err != nil {
		return err
	}
	if err := p.applyWorkspace(work, true); err != nil {
		return err
	}
	fmt.Fprintln(out, "[OK] Arquivos incorporados, inventarios validados e pacotes reconstruidos.")
	fmt.Fprintln(out, "[PROXIMO] Execute 'x86qw-content verify' e revise 'git diff'.")
	return nil
}

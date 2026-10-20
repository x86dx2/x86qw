package content

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Release candidate verification (maintenance/tools/release_candidate.py
// verify): every artifact and bound metadata file must match candidate.json
// byte for byte, and the ownership, SBOM and provenance documents must
// describe exactly those artifacts. Signed native evidence is not ported:
// a candidate carrying release-evidence.json is refused rather than
// accepted unchecked.

var (
	candidateSemver    = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$`)
	candidateUTC       = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`)
	candidateSPDXID    = regexp.MustCompile(`^SPDXRef-[A-Za-z0-9.-]+$`)
	candidateMetadata  = setOf("candidate.json", "checksums.txt", "ownership.json", "sbom.spdx.json", "provenance.json", "release-evidence.json", "evidence-root.json", "release-receipt.json")
	candidateBound     = []string{"checksums.txt", "ownership.json", "sbom.spdx.json", "provenance.json"}
	candidateManifest  = []string{"format", "project", "version", "commit", "generated_at", "artifacts", "artifact_count", "candidate_sha256", "checksums", "metadata"}
	candidateLicenses  = setOf("MIT", "NOASSERTION")
	candidateForbidden = []string{"site/public/api/v1/trust/"}
)

const (
	candidateSPDXCreator = "Tool: x86QW release-candidate"
	candidateSBOMHost    = "qw.x86.com.br"
)

func candidateRegular(path, label string) error {
	if info, err := os.Lstat(path); err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("%s ausente ou inseguro: %s", label, path)
	}
	return nil
}

func candidateSHA256(path string) (int64, string, error) {
	if err := candidateRegular(path, "arquivo do candidato"); err != nil {
		return 0, "", err
	}
	file, err := os.Open(path)
	if err != nil {
		return 0, "", fmt.Errorf("não foi possível ler o artefato do candidato: %s", path)
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return 0, "", fmt.Errorf("não foi possível ler o artefato do candidato: %s", path)
	}
	return size, hex.EncodeToString(hash.Sum(nil)), nil
}

func candidateJSON(path string) (*Object, error) {
	if err := candidateRegular(path, "metadata do candidato"); err != nil {
		return nil, err
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("metadata do candidato inválida: %s", path)
	}
	value, err := pyjson.Decode(payload, pyjson.RejectDuplicates)
	var duplicate *pyjson.DuplicateKeyError
	if errors.As(err, &duplicate) {
		return nil, fmt.Errorf("JSON contém chave duplicada: %s", duplicate.Key)
	}
	if err != nil {
		return nil, fmt.Errorf("metadata do candidato inválida: %s", path)
	}
	document, ok := value.(*Object)
	if !ok {
		return nil, fmt.Errorf("metadata do candidato precisa ser objeto: %s", path)
	}
	return document, nil
}

// candidateArtifactName mirrors _validate_artifact_name and returns the
// PurePosixPath-normalized name.
func candidateArtifactName(value any) (string, error) {
	text, ok := value.(string)
	if !ok || text == "" || strings.Contains(text, `\`) {
		return "", fmt.Errorf("caminho de artefato inválido: %s", pyRepr(value))
	}
	path := purePosix(text)
	if path.absolute || path.hasPart("..") {
		return "", fmt.Errorf("caminho de artefato inválido: %s", pyRepr(value))
	}
	if candidateMetadata[text] {
		return "", fmt.Errorf("nome reservado de metadata no candidato: %s", pyRepr(value))
	}
	return path.asPosix(), nil
}

func candidatePayloadPath(value string) error {
	for _, prefix := range candidateForbidden {
		if strings.HasPrefix(value, prefix) {
			return errors.New("metadata TUF pública não pode entrar no candidato antes do metadata-last: " + value)
		}
	}
	return nil
}

// candidateFiles mirrors _artifact_files: every regular file, refusing
// symlinks and special files.
func candidateFiles(root string) ([]string, error) {
	var files []string
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == root {
			return nil
		}
		switch {
		case entry.Type()&fs.ModeSymlink != 0:
			return fmt.Errorf("candidato não aceita symlink: %s", path)
		case entry.Type().IsRegular():
			files = append(files, path)
		case !entry.IsDir():
			return fmt.Errorf("candidato contém tipo especial: %s", path)
		}
		return nil
	})
	return files, err
}

// sizeDigest mirrors the {"size", "sha256"} identity checks.
func sizeDigest(value any) (*Object, bool) {
	identity, ok := value.(*Object)
	if !ok || !sameKeys(identity.Keys(), "size", "sha256") || !pyjson.IsExactInt(identity.Value("size")) {
		return nil, false
	}
	if cmp, _ := pyjson.IntCmp(identity.Value("size"), 0); cmp < 0 {
		return nil, false
	}
	digest, ok := identity.Value("sha256").(string)
	return identity, ok && hex64Pattern.MatchString(digest)
}

func reprList(values []string) string {
	quoted := make([]string, len(values))
	for index, value := range values {
		quoted[index] = pyRepr(value)
	}
	return "[" + strings.Join(quoted, ", ") + "]"
}

// candidateDigest is _candidate_digest: SHA-256 of the canonical manifest
// with candidate_sha256 cleared.
func candidateDigest(manifest *Object) (string, error) {
	identity := manifest.Clone()
	identity.Set("candidate_sha256", nil)
	payload, err := pyjson.EncodeFile(identity, true)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:]), nil
}

// VerifyCandidateOptions are the verify flags; the evidence ones are not ported.
type VerifyCandidateOptions struct {
	AllowPendingEvidence bool
	TrustRoot            string
}

// VerifyCandidate mirrors release_candidate.verify_candidate.
func VerifyCandidate(candidate string, options VerifyCandidateOptions) (*Object, error) {
	if options.AllowPendingEvidence || options.TrustRoot != "" {
		return nil, errors.New("evidência nativa M3 não é suportada pelo verify nativo")
	}
	if info, err := os.Lstat(candidate); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("candidato ausente ou inseguro: %s", candidate)
	}
	manifest, err := candidateJSON(filepath.Join(candidate, "candidate.json"))
	if err != nil {
		return nil, err
	}
	if !sameKeys(manifest.Keys(), candidateManifest...) {
		return nil, errors.New("manifest contém campos desconhecidos ou ausentes")
	}
	if !pyjson.IsExactInt(manifest.Value("format")) || !pyjson.EqInt(manifest.Value("format"), 2) || !pyjson.Eq(manifest.Value("project"), "x86qw") {
		return nil, errors.New("identidade do candidato inválida")
	}
	version, versionOK := manifest.Value("version").(string)
	commit, commitOK := manifest.Value("commit").(string)
	if !versionOK || !commitOK {
		return nil, errors.New("identidade do candidato incompleta")
	}
	if !candidateSemver.MatchString(version) {
		return nil, fmt.Errorf("versão SemVer inválida: %s", pyRepr(version))
	}
	if !hex40Pattern.MatchString(commit) {
		return nil, errors.New("commit precisa ser um SHA-1 hexadecimal completo")
	}
	artifacts, ok := manifest.Value("artifacts").(*Object)
	if !ok || artifacts.Len() == 0 {
		return nil, errors.New("candidato sem artefatos")
	}
	if !pyjson.Eq(manifest.Value("checksums"), "checksums.txt") {
		return nil, errors.New("manifest aponta para checksums inválido")
	}
	generatedAt, ok := manifest.Value("generated_at").(string)
	if !ok || !candidateUTC.MatchString(generatedAt) {
		return nil, errors.New("manifest sem generated_at UTC canônico")
	}
	declaredDigest, ok := manifest.Value("candidate_sha256").(string)
	if !ok || !hex64Pattern.MatchString(declaredDigest) {
		return nil, errors.New("candidate_sha256 do manifest inválido")
	}
	expectedDigest, err := candidateDigest(manifest)
	if err != nil {
		return nil, err
	}
	if declaredDigest != expectedDigest {
		return nil, errors.New("candidate_sha256 do manifest diverge")
	}

	seen := map[string]bool{}
	portable := map[string]bool{}
	normalized := map[string]*Object{}
	for _, raw := range artifacts.Keys() {
		name, err := candidateArtifactName(raw)
		if err != nil {
			return nil, err
		}
		if err := candidatePayloadPath(name); err != nil {
			return nil, err
		}
		if raw != name {
			return nil, fmt.Errorf("caminho de artefato não canônico: %s", pyRepr(raw))
		}
		key := Casefold(norm.NFC.String(name))
		if portable[key] {
			return nil, fmt.Errorf("colisão de caminho portátil no candidato: %s", name)
		}
		portable[key] = true
		path := filepath.Join(candidate, filepath.FromSlash(name))
		if info, err := os.Lstat(path); err != nil || !info.Mode().IsRegular() {
			return nil, fmt.Errorf("artefato ausente ou inseguro: %s", name)
		}
		expected, ok := sizeDigest(artifacts.Value(raw))
		if !ok {
			return nil, fmt.Errorf("metadados inválidos para artefato: %s", name)
		}
		size, digest, err := candidateSHA256(path)
		if err != nil {
			return nil, err
		}
		if !pyjson.EqInt(expected.Value("size"), size) || digest != expected.Value("sha256") {
			return nil, fmt.Errorf("artefato do candidato diverge: %s", name)
		}
		seen[name] = true
		normalized[name] = expected
	}
	if !pyjson.IsExactInt(manifest.Value("artifact_count")) || !pyjson.EqInt(manifest.Value("artifact_count"), int64(artifacts.Len())) {
		return nil, errors.New("contagem de artefatos do candidato diverge")
	}
	files, err := candidateFiles(candidate)
	if err != nil {
		return nil, err
	}
	actual := map[string]bool{}
	for _, path := range files {
		relative, _ := filepath.Rel(candidate, path)
		if relative = filepath.ToSlash(relative); !candidateMetadata[relative] {
			actual[relative] = true
		}
	}
	var unregistered []string
	for name := range actual {
		if !seen[name] {
			unregistered = append(unregistered, name)
		}
	}
	for name := range seen {
		if !actual[name] {
			unregistered = append(unregistered, name)
		}
	}
	if len(unregistered) > 0 {
		sort.Strings(unregistered)
		return nil, fmt.Errorf("arquivos não registrados no candidato: %s", reprList(unregistered))
	}

	checksums, err := os.ReadFile(filepath.Join(candidate, "checksums.txt"))
	if err != nil || !utf8.Valid(checksums) {
		return nil, errors.New("checksums.txt do candidato está ausente ou inválido")
	}
	declared := map[string]string{}
	for _, line := range pySplitlines(string(checksums)) {
		digest, rawName, found := strings.Cut(line, "  ")
		if !found || !hex64Pattern.MatchString(digest) {
			return nil, errors.New("checksums.txt contém uma linha inválida")
		}
		name, err := candidateArtifactName(rawName)
		if err != nil {
			return nil, err
		}
		if _, duplicate := declared[name]; duplicate {
			return nil, errors.New("checksums.txt contém um artefato duplicado")
		}
		declared[name] = digest
	}
	matches := len(declared) == len(normalized)
	for name, expected := range normalized {
		matches = matches && declared[name] == expected.Value("sha256")
	}
	if !matches {
		return nil, errors.New("checksums.txt diverge do manifest do candidato")
	}

	metadata, ok := manifest.Value("metadata").(*Object)
	if !ok || !sameKeys(metadata.Keys(), candidateBound...) {
		return nil, errors.New("manifest sem metadata imutável completa")
	}
	for _, name := range candidateBound {
		expected, ok := sizeDigest(metadata.Value(name))
		if !ok {
			return nil, fmt.Errorf("metadados vinculados inválidos: %s", name)
		}
		size, digest, err := candidateSHA256(filepath.Join(candidate, name))
		if err != nil {
			return nil, err
		}
		if !pyjson.EqInt(expected.Value("size"), size) || digest != expected.Value("sha256") {
			return nil, fmt.Errorf("metadata imutável diverge do manifest: %s", name)
		}
	}

	ownershipRaw, err := candidateJSON(filepath.Join(candidate, "ownership.json"))
	if err != nil {
		return nil, err
	}
	ownership, err := candidateOwnership(ownershipRaw, normalized)
	if err != nil {
		return nil, err
	}
	sbom, err := candidateJSON(filepath.Join(candidate, "sbom.spdx.json"))
	if err != nil {
		return nil, err
	}
	if err := validateCandidateSBOM(sbom, ownership, version, commit, generatedAt); err != nil {
		return nil, err
	}
	provenance, err := candidateJSON(filepath.Join(candidate, "provenance.json"))
	if err != nil {
		return nil, err
	}
	if err := validateCandidateProvenance(provenance, normalized, version, commit); err != nil {
		return nil, err
	}
	if _, err := os.Lstat(filepath.Join(candidate, "release-evidence.json")); err == nil {
		return nil, errors.New("evidência nativa M3 não é suportada pelo verify nativo")
	}
	return manifest, nil
}

func utf8Valid(payload []byte) bool {
	return norm.NFC.IsNormal(payload) || strings.ToValidUTF8(string(payload), "�") == string(payload)
}

// candidateOwnership mirrors _load_ownership_fragments for the single
// ownership.json document verify supplies.
func candidateOwnership(raw *Object, artifacts map[string]*Object) (*Object, error) {
	document, err := ValidateOwnership(raw)
	if err != nil {
		return nil, fmt.Errorf("fragmento de ownership inválido: %v", err)
	}
	ownership, err := MergeOwnership(document)
	if err != nil {
		return nil, fmt.Errorf("ownership do candidato inválido: %v", err)
	}
	declared := map[string]*Object{}
	var order []string
	for _, entry := range objects(ownership.Value("artifacts")) {
		path := pyjson.Str(entry.Value("path"))
		if _, ok := declared[path]; !ok {
			order = append(order, path)
		}
		declared[path] = entry
	}
	var missing, extra []string
	for name := range artifacts {
		if declared[name] == nil {
			missing = append(missing, name)
		}
	}
	for name := range declared {
		if artifacts[name] == nil {
			extra = append(extra, name)
		}
	}
	if len(missing) > 0 || len(extra) > 0 {
		sort.Strings(missing)
		sort.Strings(extra)
		var details []string
		if len(missing) > 0 {
			details = append(details, "faltam: "+strings.Join(missing, ", "))
		}
		if len(extra) > 0 {
			details = append(details, "extras: "+strings.Join(extra, ", "))
		}
		return nil, errors.New("ownership não cobre exatamente o candidato (" + strings.Join(details, "; ") + ")")
	}
	for _, name := range order {
		entry, expected := declared[name], artifacts[name]
		if !pyjson.Eq(entry.Value("size"), expected.Value("size")) || !pyjson.Eq(entry.Value("sha256"), expected.Value("sha256")) {
			return nil, fmt.Errorf("ownership diverge do artefato do candidato: %s", name)
		}
	}
	return ownership, nil
}

// flattenOwnership mirrors release_ownership.flatten_entries: every entry
// and nested member keyed by "path[::member...]".
func flattenOwnership(document *Object) (map[string]*Object, error) {
	normalized, err := ValidateOwnership(document)
	if err != nil {
		return nil, err
	}
	flattened := map[string]*Object{}
	var visit func(entry *Object, prefix string) error
	visit = func(entry *Object, prefix string) error {
		if flattened[prefix] != nil {
			return fmt.Errorf("ownership contém caminho SPDX duplicado: %s", prefix)
		}
		flattened[prefix] = entry
		for _, child := range objects(entry.Value("members")) {
			if err := visit(child, prefix+"::"+pyjson.Str(child.Value("path"))); err != nil {
				return err
			}
		}
		return nil
	}
	for _, entry := range objects(normalized.Value("artifacts")) {
		if err := visit(entry, pyjson.Str(entry.Value("path"))); err != nil {
			return nil, err
		}
	}
	return flattened, nil
}

func validateCandidateSBOM(sbom, ownership *Object, version, commit, generatedAt string) error {
	if !sameKeys(sbom.Keys(), "spdxVersion", "dataLicense", "SPDXID", "name", "documentNamespace", "creationInfo", "files") {
		return errors.New("SBOM contém campos desconhecidos ou ausentes")
	}
	if spdx, ok := sbom.Value("spdxVersion").(string); !ok || spdx != "SPDX-2.3" {
		return errors.New("SBOM SPDX inválido")
	}
	if !pyjson.Eq(sbom.Value("SPDXID"), "SPDXRef-DOCUMENT") || !pyjson.Eq(sbom.Value("dataLicense"), "CC0-1.0") ||
		!pyjson.Eq(sbom.Value("name"), "x86qw-"+version) ||
		!pyjson.Eq(sbom.Value("documentNamespace"), "https://"+candidateSBOMHost+"/release/"+version+"/"+commit) {
		return errors.New("SBOM não corresponde à identidade do candidato")
	}
	creation, ok := sbom.Value("creationInfo").(*Object)
	if !ok || !sameKeys(creation.Keys(), "created", "creators") || !pyjson.Eq(creation.Value("created"), generatedAt) ||
		!pyjson.Eq(creation.Value("creators"), []any{candidateSPDXCreator}) {
		return errors.New("SBOM possui creationInfo SPDX inválido")
	}
	files, ok := sbom.Value("files").([]any)
	if !ok {
		return errors.New("SBOM sem lista de arquivos")
	}
	entries, err := flattenOwnership(ownership)
	if err != nil {
		return err
	}
	seen := map[string]bool{}
	seenIDs := map[string]bool{"SPDXRef-DOCUMENT": true}
	for _, value := range files {
		item, ok := value.(*Object)
		if !ok {
			return errors.New("SBOM contém arquivo inválido")
		}
		if !sameKeys(item.Keys(), "SPDXID", "fileName", "checksums", "licenseConcluded", "copyrightText") {
			return errors.New("SBOM contém campos de arquivo desconhecidos ou ausentes")
		}
		id, ok := item.Value("SPDXID").(string)
		if !ok || !candidateSPDXID.MatchString(id) || seenIDs[id] {
			return errors.New("SBOM contém SPDXID de arquivo inválido ou duplicado")
		}
		seenIDs[id] = true
		name, ok := item.Value("fileName").(string)
		if !ok || entries[name] == nil {
			return errors.New("SBOM contém caminho de arquivo inválido ou não declarado")
		}
		if seen[name] {
			return errors.New("SBOM não corresponde aos artefatos do candidato")
		}
		seen[name] = true
		checksums, ok := item.Value("checksums").([]any)
		if !ok || len(checksums) != 1 {
			return fmt.Errorf("SBOM sem checksum único para %s", name)
		}
		checksum, ok := checksums[0].(*Object)
		if !ok || !sameKeys(checksum.Keys(), "algorithm", "checksumValue") || !pyjson.Eq(checksum.Value("algorithm"), "SHA256") {
			return fmt.Errorf("SBOM usa algoritmo inválido para %s", name)
		}
		entry := entries[name]
		if !pyjson.Eq(checksum.Value("checksumValue"), entry.Value("sha256")) {
			return fmt.Errorf("SBOM diverge do artefato %s", name)
		}
		license, licenseOK := item.Value("licenseConcluded").(string)
		copyright, copyrightOK := item.Value("copyrightText").(string)
		if !licenseOK || !candidateLicenses[license] || !pyjson.Eq(license, entry.Value("license_concluded")) ||
			!copyrightOK || !pyjson.Eq(copyright, entry.Value("copyright_text")) {
			return fmt.Errorf("SBOM possui licença/copyright incompatível para %s", name)
		}
	}
	if len(seen) != len(entries) {
		return errors.New("SBOM não lista exatamente o ownership declarado")
	}
	return nil
}

func validateCandidateProvenance(provenance *Object, artifacts map[string]*Object, version, commit string) error {
	if !sameKeys(provenance.Keys(), "format", "project", "version", "subject", "builder", "materials") {
		return errors.New("proveniência contém campos desconhecidos ou ausentes")
	}
	if !pyjson.IsExactInt(provenance.Value("format")) || !pyjson.EqInt(provenance.Value("format"), 1) ||
		!pyjson.Eq(provenance.Value("project"), "x86qw") || !pyjson.Eq(provenance.Value("version"), version) {
		return errors.New("proveniência não corresponde à identidade do candidato")
	}
	subject, ok := provenance.Value("subject").(*Object)
	if !ok || !sameKeys(subject.Keys(), "commit", "source") || !pyjson.Eq(subject.Value("commit"), commit) ||
		!pyjson.Eq(subject.Value("source"), "immutable-checkout") {
		return errors.New("proveniência não corresponde ao commit do candidato")
	}
	builder, ok := provenance.Value("builder").(*Object)
	if !ok || !sameKeys(builder.Keys(), "id", "rebuild") || !pyjson.Eq(builder.Value("id"), "x86qw/release-candidate") ||
		builder.Value("rebuild") != false {
		return errors.New("proveniência de builder inválida")
	}
	materials, ok := provenance.Value("materials").([]any)
	if !ok {
		return errors.New("proveniência sem materiais")
	}
	seen := map[string]bool{}
	for _, value := range materials {
		material, ok := value.(*Object)
		if !ok {
			return errors.New("material de proveniência inválido")
		}
		if !sameKeys(material.Keys(), "path", "size", "sha256") {
			return errors.New("material de proveniência contém campos desconhecidos ou ausentes")
		}
		name, ok := material.Value("path").(string)
		if !ok {
			return errors.New("material de proveniência possui caminho inválido")
		}
		canonical, err := candidateArtifactName(name)
		if err != nil {
			return err
		}
		if name != canonical {
			return errors.New("material de proveniência possui caminho inválido")
		}
		if seen[name] || artifacts[name] == nil {
			return errors.New("proveniência não corresponde aos artefatos do candidato")
		}
		seen[name] = true
		expected := artifacts[name]
		if !pyjson.Eq(material.Value("size"), expected.Value("size")) || !pyjson.Eq(material.Value("sha256"), expected.Value("sha256")) {
			return fmt.Errorf("proveniência diverge do artefato %s", name)
		}
	}
	if len(seen) != len(artifacts) {
		return errors.New("proveniência não lista exatamente os artefatos do candidato")
	}
	return nil
}

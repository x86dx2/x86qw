package content

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"
	"unicode/utf8"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// TUF operation drill reports (maintenance/tools/verify_tuf_operation_report.py):
// an accountable, non-secret record that the offline renewal, expiry and
// recovery drill passed for the candidate's exact catalog target.

var (
	operationCheckedAt  = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$`)
	operationTargetPath = regexp.MustCompile(`^catalog/[0-9a-f]{64}\.catalog\.json$`)
	operationRoles      = []string{"timestamp", "snapshot", "targets"}
	operationFields     = []string{"checked_at", "current_metadata_version", "expiry_failure_detected", "format", "mode", "operation", "project",
		"published", "recovery_verified", "renewed_metadata_version", "role_versions", "root_unchanged", "status", "target", "target_unchanged"}
)

const operationMaxBytes = 2 << 20

func operationBytes(path, label string) ([]byte, error) {
	if info, err := os.Lstat(path); err != nil || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("%s ausente ou inseguro: %s", label, path)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("%s não pôde ser lido: %s", label, path)
	}
	if len(payload) == 0 || len(payload) > operationMaxBytes {
		return nil, fmt.Errorf("%s excede o limite permitido", label)
	}
	return payload, nil
}

// validOperatorText mirrors tuf_operation_drill.operation_context's text rule.
func validOperatorText(value any) bool {
	text, ok := value.(string)
	if !ok || text == "" || text != pyStrip(text) || utf8.RuneCountInString(text) > 128 {
		return false
	}
	for _, r := range text {
		if r < 0x20 {
			return false
		}
	}
	return true
}

func exactIntIn(value any, minimum, maximum int64) bool {
	if !pyjson.IsExactInt(value) {
		return false
	}
	low, _ := pyjson.IntCmp(value, minimum)
	high, _ := pyjson.IntCmp(value, maximum)
	return low >= 0 && high <= 0
}

func exactIntValue(value any) (int64, bool) {
	number, ok := value.(pyjson.Number)
	if !ok || !pyjson.IsExactInt(value) {
		return 0, false
	}
	return number.Int()
}

// VerifyTUFOperationReport mirrors verify_tuf_operation_report.verify_report.
func VerifyTUFOperationReport(candidate, reportPath string, now time.Time) (*Object, error) {
	payload, err := operationBytes(reportPath, "relatório TUF")
	if err != nil {
		return nil, err
	}
	value, err := pyjson.Decode(payload, pyjson.RejectDuplicates)
	var duplicate *pyjson.DuplicateKeyError
	if errors.As(err, &duplicate) {
		return nil, fmt.Errorf("relatório TUF contém chave duplicada: %s", duplicate.Key)
	}
	if err != nil {
		return nil, errors.New("relatório TUF não é JSON UTF-8 válido")
	}
	report, ok := value.(*Object)
	if !ok {
		return nil, errors.New("relatório TUF precisa ser um objeto JSON")
	}
	if !sameKeys(report.Keys(), operationFields...) {
		return nil, errors.New("relatório TUF possui campos ausentes ou extras")
	}
	if !pyjson.EqInt(report.Value("format"), 2) || !pyjson.Eq(report.Value("project"), "x86qw") ||
		!pyjson.Eq(report.Value("status"), "drill-passed") || !pyjson.Eq(report.Value("mode"), "offline-renewal-expiry-recovery") ||
		report.Value("published") != false || report.Value("target_unchanged") != true || report.Value("root_unchanged") != true ||
		report.Value("expiry_failure_detected") != true || report.Value("recovery_verified") != true {
		return nil, errors.New("relatório TUF não comprova drill fechado")
	}
	checkedText, ok := report.Value("checked_at").(string)
	if !ok || !operationCheckedAt.MatchString(checkedText) {
		return nil, errors.New("checked_at precisa ser timestamp UTC canônico")
	}
	checkedAt, err := time.Parse("2006-01-02T15:04:05.999999Z07:00", checkedText)
	if err != nil {
		return nil, errors.New("checked_at não é timestamp válido")
	}
	if checkedAt.After(now) {
		return nil, errors.New("checked_at do drill TUF está no futuro")
	}

	current, currentOK := exactIntValue(report.Value("current_metadata_version"))
	renewed, renewedOK := exactIntValue(report.Value("renewed_metadata_version"))
	if !currentOK || current < 1 || !renewedOK || renewed <= current {
		return nil, errors.New("versões de metadata TUF não avançaram monotonicamente")
	}
	roleVersions, ok := report.Value("role_versions").(*Object)
	if !ok || !sameKeys(roleVersions.Keys(), operationRoles...) {
		return nil, errors.New("versões TUF por role ausentes ou inválidas")
	}
	var maxCurrent, maxRenewed int64
	for _, role := range operationRoles {
		values, ok := roleVersions.Value(role).(*Object)
		if !ok || !sameKeys(values.Keys(), "current", "renewed") {
			return nil, fmt.Errorf("versões TUF inválidas para %s", role)
		}
		roleCurrent, currentOK := exactIntValue(values.Value("current"))
		roleRenewed, renewedOK := exactIntValue(values.Value("renewed"))
		if !currentOK || roleCurrent < 1 || !renewedOK || roleRenewed <= roleCurrent {
			return nil, fmt.Errorf("versões TUF não avançaram para %s", role)
		}
		maxCurrent, maxRenewed = max(maxCurrent, roleCurrent), max(maxRenewed, roleRenewed)
	}
	if current != maxCurrent || renewed != maxRenewed {
		return nil, errors.New("versões agregadas divergem das versões por role")
	}

	operation, ok := report.Value("operation").(*Object)
	if !ok || !sameKeys(operation.Keys(), "custody_host", "key_scope", "operator", "timestamp_sla_hours") {
		return nil, errors.New("contexto operacional TUF ausente ou inválido")
	}
	if !validOperatorText(operation.Value("operator")) || !validOperatorText(operation.Value("custody_host")) ||
		!exactIntIn(operation.Value("timestamp_sla_hours"), 1, 8760) {
		return nil, errors.New("contexto operacional TUF inválido")
	}
	if !pyjson.Eq(operation.Value("key_scope"), "root-and-targets-offline") {
		return nil, errors.New("escopo de chaves TUF diverge do contrato")
	}

	target, ok := report.Value("target").(*Object)
	if !ok || !sameKeys(target.Keys(), "path", "sha256", "size") {
		return nil, errors.New("target TUF ausente ou inválido")
	}
	digest, digestOK := target.Value("sha256").(string)
	path, pathOK := target.Value("path").(string)
	if !exactIntIn(target.Value("size"), 1, operationMaxBytes) || !digestOK || !hex64Pattern.MatchString(digest) ||
		!pathOK || !operationTargetPath.MatchString(path) || path != "catalog/"+digest+".catalog.json" {
		return nil, errors.New("identidade do target TUF inválida")
	}
	catalog, err := operationBytes(filepath.Join(candidate, "catalog.json"), "catálogo do candidato")
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(catalog)
	if !pyjson.EqInt(target.Value("size"), int64(len(catalog))) || digest != hex.EncodeToString(sum[:]) {
		return nil, errors.New("target TUF não corresponde ao catálogo do candidato")
	}
	return report, nil
}

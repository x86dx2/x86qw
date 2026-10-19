package receipt

import (
	"fmt"
	"net/url"
	"path"
	"regexp"
	"strings"
)

var (
	stableVersion  = regexp.MustCompile(`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
	nightlyVersion = regexp.MustCompile(`^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$`)
	clientFields   = []string{
		"format", "platform", "architecture", "channel", "selection", "install_name",
		"bundle_version", "artifact_name", "artifact_url", "artifact_sha256", "binary_sha256",
	}
)

// ClientContext is the layout an ezQuake receipt must describe.
type ClientContext struct {
	Platform, Architecture, Channel, InstallName, StableArchive, NightlySuffix string
}

// ParseClient validates an ezQuake receipt like receipts.parse_ezquake_receipt.
func ParseClient(payload []byte, context ClientContext) (*Receipt, error) {
	rec, err := ParseReceipt(payload)
	if err != nil {
		return nil, err
	}
	if len(rec.Keys) != len(clientFields) {
		return nil, fmt.Errorf("tabela de recibo ezQuake incompleta")
	}
	for _, field := range clientFields {
		if _, ok := rec.Values[field]; !ok {
			return nil, fmt.Errorf("tabela de recibo ezQuake incompleta: %s", field)
		}
	}
	if rec.Get("platform") != context.Platform || rec.Get("architecture") != context.Architecture {
		return nil, fmt.Errorf("metadados de plataforma inválidos no recibo ezQuake")
	}
	if rec.Get("channel") != context.Channel || rec.Get("install_name") != context.InstallName {
		return nil, fmt.Errorf("metadados de destino inválidos no recibo ezQuake")
	}
	for _, field := range []string{"artifact_sha256", "binary_sha256"} {
		if !hex64.MatchString(rec.Get(field)) {
			return nil, fmt.Errorf("%s inválido no recibo ezQuake", field)
		}
	}
	selection, bundle := rec.Get("selection"), rec.Get("bundle_version")
	var expectedName string
	switch context.Channel {
	case "stable":
		if !stableVersion.MatchString(selection) || bundle != selection {
			return nil, fmt.Errorf("seleção stable inválida no recibo ezQuake: %s", selection)
		}
		expectedName = context.StableArchive
	case "nightly":
		if !nightlyVersion.MatchString(selection) {
			return nil, fmt.Errorf("seleção nightly inválida no recibo ezQuake: %s", selection)
		}
		if context.Platform == "macos" {
			revision := selection[strings.LastIndex(selection, "_")+1:]
			if !strings.Contains(bundle, "-g"+revision) {
				return nil, fmt.Errorf("versão do bundle nightly diverge da seleção ezQuake")
			}
		} else if bundle != selection {
			return nil, fmt.Errorf("versão nightly diverge da seleção ezQuake")
		}
		expectedName = selection + context.NightlySuffix
	default:
		return nil, fmt.Errorf("canal ezQuake inválido: %s", context.Channel)
	}
	parsed, err := url.Parse(rec.Get("artifact_url"))
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return nil, fmt.Errorf("URL do artefato inválida no recibo ezQuake")
	}
	name, _ := url.PathUnescape(path.Base(parsed.Path))
	if rec.Get("artifact_name") != expectedName || name != expectedName {
		return nil, fmt.Errorf("artefato inesperado no recibo ezQuake")
	}
	return rec, nil
}

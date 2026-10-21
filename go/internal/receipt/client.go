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
		return nil, fmt.Errorf("incomplete ezQuake receipt table")
	}
	for _, field := range clientFields {
		if _, ok := rec.Values[field]; !ok {
			return nil, fmt.Errorf("incomplete ezQuake receipt table: %s", field)
		}
	}
	if rec.Get("platform") != context.Platform || rec.Get("architecture") != context.Architecture {
		return nil, fmt.Errorf("invalid platform metadata in the ezQuake receipt")
	}
	if rec.Get("channel") != context.Channel || rec.Get("install_name") != context.InstallName {
		return nil, fmt.Errorf("invalid target metadata in the ezQuake receipt")
	}
	for _, field := range []string{"artifact_sha256", "binary_sha256"} {
		if !hex64.MatchString(rec.Get(field)) {
			return nil, fmt.Errorf("invalid %s in the ezQuake receipt", field)
		}
	}
	selection, bundle := rec.Get("selection"), rec.Get("bundle_version")
	var expectedName string
	switch context.Channel {
	case "stable":
		if !stableVersion.MatchString(selection) || bundle != selection {
			return nil, fmt.Errorf("invalid stable selection in the ezQuake receipt: %s", selection)
		}
		expectedName = context.StableArchive
	case "nightly":
		if !nightlyVersion.MatchString(selection) {
			return nil, fmt.Errorf("invalid nightly selection in the ezQuake receipt: %s", selection)
		}
		if context.Platform == "macos" {
			revision := selection[strings.LastIndex(selection, "_")+1:]
			if !strings.Contains(bundle, "-g"+revision) {
				return nil, fmt.Errorf("the nightly bundle version does not match the ezQuake selection")
			}
		} else if bundle != selection {
			return nil, fmt.Errorf("the nightly version does not match the ezQuake selection")
		}
		expectedName = selection + context.NightlySuffix
	default:
		return nil, fmt.Errorf("invalid ezQuake channel: %s", context.Channel)
	}
	parsed, err := url.Parse(rec.Get("artifact_url"))
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return nil, fmt.Errorf("invalid artifact URL in the ezQuake receipt")
	}
	name, _ := url.PathUnescape(path.Base(parsed.Path))
	if rec.Get("artifact_name") != expectedName || name != expectedName {
		return nil, fmt.Errorf("unexpected artifact in the ezQuake receipt")
	}
	return rec, nil
}

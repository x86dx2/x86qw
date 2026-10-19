package tufsign

import (
	"strings"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
)

type localFetcher struct{ files map[string][]byte }

func (f *localFetcher) DownloadFile(url string, maxLength int64, _ time.Duration) ([]byte, error) {
	name := strings.TrimPrefix(url, "https://local.invalid/")
	data, ok := f.files[name]
	if !ok {
		return nil, &metadata.ErrDownloadHTTP{StatusCode: 404, URL: url}
	}
	return data, nil
}

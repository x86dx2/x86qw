package download

import (
	"os/exec"
	"strings"
)

// darwinUserCacheDir asks the system like `getconf DARWIN_USER_CACHE_DIR`.
func darwinUserCacheDir() string {
	output, err := exec.Command("/usr/bin/getconf", "DARWIN_USER_CACHE_DIR").Output()
	if err != nil {
		return ""
	}
	return strings.TrimRight(strings.TrimSpace(string(output)), "/")
}

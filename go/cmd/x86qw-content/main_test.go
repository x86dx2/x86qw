package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, _ := os.Getwd()
	root, err := findRoot(dir)
	if err != nil {
		t.Skip("checkout unavailable")
	}
	return root
}

func TestExitCodesFollowManage(t *testing.T) {
	root := repoRoot(t)
	cases := []struct {
		args []string
		code int
		err  string
	}{
		{nil, exitUsage, "required: command"},
		{[]string{"bogus"}, exitUsage, "invalid choice"},
		{[]string{"--root", root, "verify", "--nope"}, exitUsage, "flag provided but not defined"},
		{[]string{"--root", root, "publish", "--github-only", "--gitlab-only"}, exitUsage, "not allowed"},
		{[]string{"--root", root, "add"}, exitUsage, "required: definition"},
		{[]string{"--root", root, "add", filepath.Join(t.TempDir(), "missing.json"), "--dry-run"}, exitError, "[ERROR] could not read"},
		{[]string{"--root", root, "update", "--workers", "0"}, exitError, "[ERROR] --workers must be between 1 and 32"},
		{[]string{"--root", root, "check", "--offline"}, exitOK, ""},
		{[]string{"--root", root, "verify", "--help"}, exitOK, ""},
	}
	for _, item := range cases {
		var stdout, stderr bytes.Buffer
		if code := run(item.args, &stdout, &stderr); code != item.code || !strings.Contains(stderr.String(), item.err) {
			t.Errorf("%v: exit %d, stderr %q", item.args, code, stderr.String())
		}
	}
}

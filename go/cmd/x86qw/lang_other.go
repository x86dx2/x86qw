//go:build !windows

package main

// systemLanguage is empty where the locale lives in the environment.
func systemLanguage() string { return "" }

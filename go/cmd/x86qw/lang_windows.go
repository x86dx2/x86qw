package main

import "golang.org/x/sys/windows"

// systemLanguage is the user's first display language, such as pt-BR.
func systemLanguage() string {
	languages, err := windows.GetUserPreferredUILanguages(windows.MUI_LANGUAGE_NAME)
	if err != nil || len(languages) == 0 {
		return ""
	}
	return languages[0]
}

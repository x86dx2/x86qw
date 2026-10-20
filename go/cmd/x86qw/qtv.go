package main

import (
	"io"
)

func runQtv(args []string, stdout, stderr io.Writer) int {
	code, _ := serviceMain("qtv", args, stdout, stderr, false)
	return code
}

func init() {
	register("qtv", 77, "qtv                  inicia o relay QTV", noName(runQtv))
}

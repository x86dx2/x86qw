package main

import (
	"io"
)

func runQtv(args []string, stdout, stderr io.Writer) int {
	return serviceMain("qtv", args, stdout, stderr)
}

func init() {
	register("qtv", 77, "qtv                  inicia o relay QTV", noName(runQtv))
}

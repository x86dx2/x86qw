package main

import (
	"io"
)

func runProxy(args []string, stdout, stderr io.Writer) int {
	code, _ := serviceMain("proxy", args, stdout, stderr, false)
	return code
}

func init() {
	register("proxy", 76, "proxy                inicia o proxy QWFWD", noName(runProxy))
}

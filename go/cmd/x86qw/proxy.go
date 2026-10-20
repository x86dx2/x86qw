package main

import (
	"io"
)

func runProxy(args []string, stdout, stderr io.Writer) int {
	return serviceMain("proxy", args, stdout, stderr)
}

func init() {
	register("proxy", 76, "proxy                inicia o proxy QWFWD", noName(runProxy))
}

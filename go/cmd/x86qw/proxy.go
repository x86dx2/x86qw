package main

import (
	"io"

	"github.com/x86dx2/x86qw/go/internal/services"
)

func runProxy(args []string, stdout, stderr io.Writer) int {
	options, err := services.ParseProxy(args)
	if err != nil {
		return serviceUsage("proxy", err, stderr)
	}
	op := &services.Operation{Action: "proxy", Proxy: options, Args: args}
	return runService(op, &options.Common, stdout, stderr)
}

func init() {
	register("proxy", 76, "proxy                inicia o proxy QWFWD", noName(runProxy))
}

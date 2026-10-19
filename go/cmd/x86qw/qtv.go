package main

import (
	"io"

	"github.com/x86dx2/x86qw/go/internal/services"
)

func runQtv(args []string, stdout, stderr io.Writer) int {
	options, err := services.ParseQtv(args)
	if err != nil {
		return serviceUsage("qtv", err, stderr)
	}
	op := &services.Operation{Action: "qtv", Qtv: options, Args: args}
	return runService(op, &options.Common, stdout, stderr)
}

func init() {
	register("qtv", 77, "qtv                  inicia o relay QTV", noName(runQtv))
}

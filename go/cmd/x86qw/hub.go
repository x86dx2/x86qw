package main

import (
	"context"
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/hub"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runHub(args []string, stdout, stderr io.Writer) int {
	a, target, code, ok := parseTarget("hub", args, stdout, stderr)
	if !ok {
		return code
	}
	servers, err := hub.Servers(context.Background(), target)
	if err != nil {
		if a.json {
			output.Failure("hub", output.ExitFailure, "operation", err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	if a.json {
		data := map[string]any{"servers": servers, "target": target}
		if err := output.Success("hub", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	for _, server := range servers {
		fmt.Fprintf(stdout, "  %-28s %-6s %-10s %2d+%-2d %s\n", server.Address, server.Mode, server.Map, server.Players.Humans, server.Players.Bots, server.Title)
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "hub", group: "Play", order: 30, synopsis: "[TARGET]",
		summary: "lists the public servers to play or watch",
		flags:   (&targetArgs{}).parser, run: noName(runHub)})
}

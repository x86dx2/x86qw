package main

import (
	"fmt"
	"io"
	"path/filepath"
	"strings"
)

// parsedArgs is an argparse-like parse: options may come before or after
// the positional target, as the Python launcher passes them.
type parsedArgs struct {
	values map[string]string
	bools  map[string]bool
	target string
}

// optionalValue marks flags whose value may be omitted (argparse nargs="?").
type optionalValue struct{ name, fallback string }

// parseArgs accepts value flags, boolean flags and optional-value flags, plus
// at most one positional target (or --target).
func parseArgs(name string, args []string, valueFlags, boolFlags []string, optional []optionalValue, stderr io.Writer) (*parsedArgs, bool) {
	values, bools := map[string]bool{}, map[string]bool{}
	for _, flag := range valueFlags {
		values[flag] = true
	}
	for _, flag := range boolFlags {
		bools[flag] = true
	}
	optionals := map[string]string{}
	for _, item := range optional {
		optionals[item.name] = item.fallback
	}
	result := &parsedArgs{values: map[string]string{}, bools: map[string]bool{}}
	var positional []string
	fail := func(format string, args ...any) (*parsedArgs, bool) {
		fmt.Fprintf(stderr, "x86qw %s: erro: %s\n", name, fmt.Sprintf(format, args...))
		return nil, false
	}
	for index := 0; index < len(args); index++ {
		arg := args[index]
		flag, inline, hasInline := strings.Cut(arg, "=")
		switch {
		case flag == "--target" || values[flag]:
			value := inline
			if !hasInline {
				if index+1 >= len(args) {
					return fail("argumento %s: esperado um valor", flag)
				}
				index++
				value = args[index]
			}
			result.values[flag] = value
		case optionals[flag] != "" || hasOptional(optional, flag):
			value := optionals[flag]
			if hasInline {
				value = inline
			} else if index+1 < len(args) && !strings.HasPrefix(args[index+1], "-") {
				index++
				value = args[index]
			}
			result.values[flag] = value
		case bools[arg] || arg == "--json":
			result.bools[arg] = true
		case strings.HasPrefix(arg, "-") && arg != "-":
			return fail("argumentos não reconhecidos: %s", arg)
		default:
			positional = append(positional, arg)
		}
	}
	if len(positional) > 1 {
		return fail("argumentos não reconhecidos: %s", strings.Join(positional[1:], " "))
	}
	target := result.values["--target"]
	if len(positional) == 1 {
		if target != "" && filepath.Clean(target) != filepath.Clean(positional[0]) {
			return fail("--target e o destino posicional não podem divergir")
		}
		target = positional[0]
	}
	if target == "" {
		target = defaultTarget()
	}
	if absolute, err := filepath.Abs(target); err == nil {
		target = absolute
	}
	result.target = target
	return result, true
}

func hasOptional(optional []optionalValue, name string) bool {
	for _, item := range optional {
		if item.name == name {
			return true
		}
	}
	return false
}

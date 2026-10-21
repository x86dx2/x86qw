// Package cliargs is the one argument parser of the x86qw commands. Flags
// go in any position, as --flag VALUE or --flag=VALUE; "--" ends them;
// -h and --help ask for the command's help; mutually exclusive flags are
// refused together. Everything else is positional.
package cliargs

import (
	"errors"
	"flag"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// UsageError is a mistake in the command line (exit 2).
type UsageError struct{ msg string }

func (e *UsageError) Error() string { return e.msg }

// Usagef builds a UsageError.
func Usagef(format string, args ...any) error {
	return &UsageError{msg: fmt.Sprintf(format, args...)}
}

// IsUsage reports whether err is a usage error.
func IsUsage(err error) bool {
	var target *UsageError
	return errors.As(err, &target)
}

// ErrHelp is what Parse returns when the command line asks for help.
var ErrHelp = errors.New("ajuda pedida")

type flagSpec struct {
	names    []string
	meta     string // the value's placeholder; empty for a boolean flag
	help     string
	optional string // the value an optional-value flag takes when bare
	apply    func(string) error
	hidden   bool
}

// Parser holds a command's flags and, once parsed, its positionals.
type Parser struct {
	specs      map[string]*flagSpec
	order      []*flagSpec
	groups     [][]string
	seen       map[string]bool
	positional []string
}

// New returns an empty parser.
func New() *Parser { return &Parser{specs: map[string]*flagSpec{}, seen: map[string]bool{}} }

func (p *Parser) add(spec *flagSpec) {
	for _, name := range spec.names {
		p.specs[name] = spec
	}
	p.order = append(p.order, spec)
}

// Bool declares a flag that sets target.
func (p *Parser) Bool(target *bool, help string, names ...string) {
	p.add(&flagSpec{names: names, help: help, apply: func(string) error { *target = true; return nil }})
}

// Value declares a flag whose value apply takes; meta names the value in
// the help.
func (p *Parser) Value(apply func(string) error, meta, help string, names ...string) {
	p.add(&flagSpec{names: names, meta: meta, help: help, apply: apply})
}

// String declares a flag that stores its value in target.
func (p *Parser) String(target *string, meta, help string, names ...string) {
	p.Value(func(value string) error { *target = value; return nil }, meta, help, names...)
}

// Int declares a flag that takes an integer between minimum and maximum.
func (p *Parser) Int(target *int, minimum, maximum int, meta, help string, names ...string) {
	p.Value(func(value string) error {
		parsed, err := strconv.Atoi(strings.TrimSpace(value))
		if err != nil {
			return Usagef("deve ser um número inteiro")
		}
		if parsed < minimum || parsed > maximum {
			return Usagef("deve estar entre %d e %d", minimum, maximum)
		}
		*target = parsed
		return nil
	}, meta, help, names...)
}

// Choice declares a flag that takes one of choices.
func (p *Parser) Choice(target *string, choices []string, help string, names ...string) {
	p.Value(func(value string) error {
		for _, choice := range choices {
			if choice == value {
				*target = value
				return nil
			}
		}
		return Usagef("escolha inválida: '%s' (use %s)", value, strings.Join(choices, ", "))
	}, strings.Join(choices, "|"), help, names...)
}

// Optional declares a flag whose value may be left out, taking bare.
// --flag=VALUE always gives a value; --flag VALUE does when VALUE does not
// start with "-".
func (p *Parser) Optional(target *string, bare, meta, help string, names ...string) {
	p.add(&flagSpec{names: names, meta: "[" + meta + "]", help: help, optional: bare,
		apply: func(value string) error { *target = value; return nil }})
}

// Hidden keeps an already declared flag out of the help.
func (p *Parser) Hidden(name string) {
	if spec := p.specs[name]; spec != nil {
		spec.hidden = true
	}
}

// Exclusive refuses any two of names together.
func (p *Parser) Exclusive(names ...string) { p.groups = append(p.groups, names) }

// Given reports whether name appeared on the command line.
func (p *Parser) Given(name string) bool {
	if spec := p.specs[name]; spec != nil {
		for _, alias := range spec.names {
			if p.seen[alias] {
				return true
			}
		}
	}
	return false
}

// Positional is what was not a flag, in order.
func (p *Parser) Positional() []string { return p.positional }

// Parse reads args. It returns ErrHelp for -h or --help, or a UsageError.
func (p *Parser) Parse(args []string) error {
	for index := 0; index < len(args); index++ {
		arg := args[index]
		if arg == "--" {
			p.positional = append(p.positional, args[index+1:]...)
			break
		}
		if arg == "-h" || arg == "--help" {
			return ErrHelp
		}
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			p.positional = append(p.positional, arg)
			continue
		}
		name, inline, hasInline := strings.Cut(arg, "=")
		spec, ok := p.specs[name]
		if !ok {
			return Usagef("opção desconhecida: %s", arg)
		}
		value := ""
		switch {
		case spec.meta == "":
			if hasInline {
				return Usagef("%s não recebe valor", name)
			}
		case spec.optional != "":
			value = spec.optional
			if hasInline {
				value = inline
			} else if index+1 < len(args) && !strings.HasPrefix(args[index+1], "-") {
				index++
				value = args[index]
			}
		case hasInline:
			value = inline
		default:
			if index+1 >= len(args) {
				return Usagef("%s precisa de um valor", name)
			}
			index++
			value = args[index]
		}
		for _, group := range p.groups {
			if !contains(group, name) {
				continue
			}
			for _, other := range group {
				if other != name && p.Given(other) && !contains(spec.names, other) {
					return Usagef("%s não pode ser usado com %s", name, other)
				}
			}
		}
		p.seen[name] = true
		if err := spec.apply(value); err != nil {
			if IsUsage(err) {
				return Usagef("%s: %s", name, err.Error())
			}
			return err
		}
	}
	return nil
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

// Flags renders the declared flags as an aligned table, one per line.
func (p *Parser) Flags() string {
	type row struct{ left, help string }
	var rows []row
	width := 0
	for _, spec := range p.order {
		if spec.hidden {
			continue
		}
		names := append([]string{}, spec.names...)
		sort.SliceStable(names, func(a, b int) bool { return len(names[a]) < len(names[b]) })
		left := strings.Join(names, ", ")
		if spec.meta != "" {
			left += " " + spec.meta
		}
		rows = append(rows, row{left, spec.help})
		width = max(width, utf8.RuneCountInString(left))
	}
	var builder strings.Builder
	for _, item := range rows {
		padding := strings.Repeat(" ", width-utf8.RuneCountInString(item.left))
		fmt.Fprintf(&builder, "  %s%s  %s\n", item.left, padding, item.help)
	}
	return builder.String()
}

// Adopt declares every flag of fs on p, as --name, so code written for
// package flag parses here too; fs.Visit still reports what was given.
func (p *Parser) Adopt(fs *flag.FlagSet) {
	fs.VisitAll(func(f *flag.Flag) {
		name := "--" + f.Name
		set := func(value string) error {
			if err := fs.Set(f.Name, value); err != nil {
				return Usagef("valor inválido: %q", value)
			}
			return nil
		}
		if boolean, ok := f.Value.(interface{ IsBoolFlag() bool }); ok && boolean.IsBoolFlag() {
			p.add(&flagSpec{names: []string{name}, help: f.Usage, apply: func(string) error { return set("true") }})
			return
		}
		meta := "VALOR"
		if kind, _ := flag.UnquoteUsage(f); kind == "int" || kind == "float" {
			meta = "N"
		}
		p.Value(set, meta, f.Usage, name)
	})
}

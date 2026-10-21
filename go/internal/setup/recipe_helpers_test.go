package setup

import (
	"flag"
	"io"
)

// parseRecipe reads a recipe's arguments (after `x86qw setup`) the way the
// command does.
func parseRecipe(args []string) (Options, error) {
	flags := flag.NewFlagSet("setup", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	read := AddRecipeFlags(flags)
	target := flags.String("target", "", "")
	flags.Bool("yes", false, "")
	if err := flags.Parse(args); err != nil {
		return Options{}, err
	}
	return read(*target)
}

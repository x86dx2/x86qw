package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/setup"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// setupArgs are the arguments of setup and install.
type setupArgs struct {
	target, profile, platform, channel, release, catalogFile string
	yes, nonInteractive                                      bool
	// recipe holds the --intent recipe flags (setup only).
	recipe     *flag.FlagSet
	readRecipe func(target string) (setup.Options, error)
}

func (a *setupArgs) parser(withRecipe bool) *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Choice(&a.profile, []string{"essential", "recommended", "complete"}, "installs this profile without opening the app", "--profile")
	p.Choice(&a.platform, []string{"macos", "linux", "windows"}, "the installation's system (default: this one)", "--platform")
	p.Choice(&a.channel, catalog.Channels, "ezQuake channel", "--channel")
	catalogFileFlag(p, &a.catalogFile)
	if withRecipe {
		a.recipe = flag.NewFlagSet("setup", flag.ContinueOnError)
		a.readRecipe = setup.AddRecipeFlags(a.recipe)
		p.Adopt(a.recipe)
		p.Bool(&a.yes, "installs the recipe without asking", "--yes")
	} else {
		p.String(&a.release, "VERSION", "ezQuake version (default: the catalog's)", "--release")
		p.Bool(&a.nonInteractive, "never opens the app; needs --profile", "--non-interactive")
	}
	return p
}

// parseSetup parses setup or install and resolves the installation.
func parseSetup(name string, args []string, stdout, stderr io.Writer) (*setupArgs, string, int, bool) {
	a := &setupArgs{platform: install.HostPlatform(), channel: "stable"}
	p := a.parser(name == "setup")
	if code, ok := parse(name, p, args, stdout, stderr); !ok {
		return nil, "", code, false
	}
	root, err := targetOf(p, a.target)
	if err != nil {
		return nil, "", usageFailure(name, err, stderr), false
	}
	return a, root, output.ExitSuccess, true
}

// recipeGiven is whether the command line carries an --intent recipe.
func (a *setupArgs) recipeGiven() bool {
	given := false
	if a.recipe != nil {
		a.recipe.Visit(func(f *flag.Flag) { given = given || f.Name == "intent" || f.Name == "recipe" })
	}
	return given
}

// installProfile installs a profile into a new installation, the work of
// setup --profile and install --profile.
func installProfile(root string, a *setupArgs, stdout, stderr io.Writer) error {
	parsed, err := loadCatalog(a.catalogFile)
	if err != nil {
		return err
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return err
	}
	result, err := install.Install(context.Background(), root, parsed, inventory, install.InstallOptions{
		Platform: a.platform, Channel: a.channel, Release: a.release, Profile: a.profile,
	})
	if err != nil {
		return err
	}
	for _, warning := range result.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	fmt.Fprintf(stdout, "✓ ezQuake + %d components installed in %s · %d files\n", len(result.Installed), root, result.Files)
	return nil
}

// failure reports err, naming a permission problem plainly, and returns 1.
func failure(root string, err error, stderr io.Writer) int {
	if errors.Is(err, os.ErrPermission) {
		fmt.Fprintf(stderr, "✗ no permission to write to %s\n", root)
	} else {
		fmt.Fprintf(stderr, "✗ %v\n", err)
	}
	return output.ExitFailure
}

func runSetup(args []string, stdout, stderr io.Writer) int {
	a, root, code, ok := parseSetup("setup", args, stdout, stderr)
	if !ok {
		return code
	}
	switch {
	case a.recipeGiven():
		// A recipe installs without the interface.
		options, err := a.readRecipe(root)
		if err != nil {
			return usageFailure("setup", err, stderr)
		}
		backend := &setup.Real{Trust: trust.Options{CacheDir: defaultCacheDir()}, CatalogFile: a.catalogFile, Platform: a.platform,
			Publish:   func(target string) error { return publishCLI(target, false, io.Discard) },
			Integrate: setup.Integrate}
		return runRecipe(backend, options, a.yes, stdout, stderr)
	case a.profile == "" && interactive():
		return runApp(realBackend(a.catalogFile, a.platform), root, "", stdout, stderr)
	case install.Installed(root):
		// Adopting an installation: this CLI becomes its CLI, unless it is
		// older than the installed one or a development build.
		adopted := cli.Replaces(buildinfo.Version, root, true)
		held, err := lock.Acquire(root, "install")
		if err != nil {
			return failure(root, err, stderr)
		}
		err = publishCLI(root, true, stdout)
		held.Release()
		if err != nil {
			return failure(root, err, stderr)
		}
		if _, err := install.Verify(root); err != nil {
			fmt.Fprintf(stderr, "! Verification found problems: %v\n  Run: x86qw repair\n", err)
			return output.ExitFailure
		}
		if !adopted {
			fmt.Fprintf(stdout, "· The installation in %s keeps CLI %s: this one, %s, is older or a development build.\n",
				root, cli.InstalledVersion(root), buildinfo.Version)
			return output.ExitSuccess
		}
		fmt.Fprintf(stdout, "✓ The installation in %s now uses x86QW CLI %s.\n", root, buildinfo.Version)
		return output.ExitSuccess
	case a.profile == "":
		return usageFailure("setup", cliargs.Usagef("without a terminal, give --profile or a recipe (--intent … --yes)"), stderr)
	}
	if err := installProfile(root, a, stdout, stderr); err != nil {
		return failure(root, err, stderr)
	}
	if err := publishCLI(root, true, stdout); err != nil {
		return failure(root, err, stderr)
	}
	fmt.Fprintf(stdout, "  Play now: %s play\n", here(root))
	return output.ExitSuccess
}

// here is the command to type in this terminal: on Windows the PATH
// reaches new terminals only, so it is the full path of x86qw.cmd.
func here(root string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(root, "x86qw.cmd")
	}
	return "x86qw"
}

// runRecipe installs options without the interface, printing the
// preflight, each component as it lands and how to start.
func runRecipe(backend *setup.Real, options setup.Options, yes bool, stdout, stderr io.Writer) int {
	con := newConsole(stdout, stderr, false, false)
	recipe := strings.Join(options.Recipe(), " ")
	if proceed, code := confirm("Install in "+options.Target+" with this recipe?", recipe, yes, stdout, stderr); !proceed {
		return code
	}
	// Ctrl+C cancels and waits for the rollback instead of exiting midway.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	last, err := setup.RunRecipe(ctx, backend, options, setup.RecipeReport{
		Check: func(check setup.Check) {
			text := check.Title + "  " + check.Value
			switch check.State {
			case setup.CheckFailed:
				con.failure(text + " — " + strings.TrimSpace(check.Detail+" "+check.Fix))
			case setup.CheckWarning:
				con.warning(text + " — " + strings.TrimSpace(check.Detail+" "+check.Fix))
			default:
				con.success(text)
			}
		},
		Landed: func(lane setup.Lane) { con.success(fmt.Sprintf("%s · %d files", lane.Label, lane.Files)) },
	})
	switch {
	case errors.Is(err, setup.ErrBlocked):
		return output.ExitFailure
	case errors.Is(err, context.Canceled):
		con.failure("Install canceled; the folder is back to what it was.")
		return output.ExitInterrupted
	case err != nil:
		con.failure(err.Error() + " — the folder is back to what it was.")
		return output.ExitFailure
	}
	for _, warning := range last.Warnings {
		con.warning(warning)
	}
	fmt.Fprintf(stdout, "✓ x86QW installed in %s\n", options.Target)
	if options.Intent.WantsServer() {
		fmt.Fprintf(stdout, "  Start the server: %s %s\n", here(options.Target), strings.Join(setup.HostCommand(options.Target, options), " "))
		fmt.Fprintf(stdout, "  rcon: %s (kept in %s)\n", options.Server.Rcon, filepath.Join(options.Target, filepath.FromSlash(setup.RconFile)))
	}
	if options.Intent.WantsClient() {
		fmt.Fprintf(stdout, "  Play now: %s play\n", here(options.Target))
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "setup", group: "Install and maintain", order: 10, synopsis: "[TARGET]",
		summary: "installs x86QW or adopts an existing installation",
		about: "On a terminal, opens the app's installer. With --profile, installs without asking; with a recipe (--intent and the other options " +
			"the installer copies), installs what it says. On an existing installation, switches it to this CLI.",
		flags: func() *cliargs.Parser { return (&setupArgs{}).parser(true) },
		run:   noName(runSetup)})
}

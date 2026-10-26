//go:build !windows

package cli

// refreshHandlers has nothing to rewrite here: the Linux handler is a
// desktop entry, which RefreshEntries rewrites, and macOS's is an applet.
func refreshHandlers(root string) {}

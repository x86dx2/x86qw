package setup

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
)

// cancelledInPreflight is the demo backend with Ctrl+C pressed during the
// preflight; it records whether the install started anyway.
type cancelledInPreflight struct {
	*Demo
	cancel    context.CancelFunc
	installed bool
}

func (b *cancelledInPreflight) Preflight(ctx context.Context, o Options) <-chan Check {
	out := make(chan Check)
	go func() {
		defer close(out)
		out <- found{ID: "folder", Title: Say("check.folder"), State: CheckOK}.check()
		b.cancel()
	}()
	return out
}

func (b *cancelledInPreflight) Install(ctx context.Context, o Options, control <-chan bool) <-chan Progress {
	b.installed = true
	return failed(ctx.Err())
}

func TestRunRecipeStopsWhenCancelledInThePreflight(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	b := &cancelledInPreflight{Demo: &Demo{}, cancel: cancel}
	_, err := RunRecipe(ctx, b, Defaults(Play, filepath.Join(t.TempDir(), "x86qw")), RecipeReport{})
	if !errors.Is(err, context.Canceled) || b.installed {
		t.Fatalf("installed %v, err %v", b.installed, err)
	}
}

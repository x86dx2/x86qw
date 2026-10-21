package setup

import (
	"context"
	"errors"
)

// ErrBlocked is RunRecipe's answer when a preflight check failed.
var ErrBlocked = errors.New("the preflight found a problem; nothing was installed")

// RecipeReport is what RunRecipe tells as it goes; nil fields are skipped.
type RecipeReport struct {
	// Check is each finished preflight check.
	Check func(Check)
	// Landed is each component once all its files are in.
	Landed func(Lane)
}

// RunRecipe installs options without the interface: the preflight first,
// then the install, unless a check failed. It returns the last progress;
// a cancelled ctx rolls the folder back and ends with context.Canceled.
func RunRecipe(ctx context.Context, b Backend, options Options, report RecipeReport) (Progress, error) {
	blocked := false
	for check := range b.Preflight(ctx, options) {
		if check.State == CheckRunning {
			continue
		}
		blocked = blocked || check.State == CheckFailed
		if report.Check != nil {
			report.Check(check)
		}
	}
	if blocked {
		return Progress{}, ErrBlocked
	}
	landed := map[string]bool{}
	var last Progress
	for progress := range b.Install(ctx, options, nil) {
		for _, lane := range progress.Lanes {
			if !landed[lane.ID] && lane.FileCount > 0 && lane.Files >= lane.FileCount {
				landed[lane.ID] = true
				if report.Landed != nil {
					report.Landed(lane)
				}
			}
		}
		last = progress
	}
	return last, last.Err
}

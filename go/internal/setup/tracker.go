package setup

import (
	"context"
	"sync"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
)

// tracker turns what an installation reports into Progress snapshots:
// lanes, totals, a smoothed speed, the file log and the pause.
type tracker struct {
	mu       sync.Mutex
	started  time.Time
	progress Progress
	index    map[string]int
	labels   map[string]string
	// speed is measured from the bytes seen at the previous tick.
	lastDone int64
	lastAt   time.Time
	wanted   bool          // a pause was asked for
	resume   chan struct{} // closed when the pause ends
}

const logLimit = 400

func newTracker(lanes []Lane, labels map[string]string) *tracker {
	t := &tracker{started: time.Now(), index: map[string]int{}, labels: labels, resume: make(chan struct{})}
	for _, lane := range lanes {
		t.index[lane.ID] = len(t.progress.Lanes)
		t.progress.Lanes = append(t.progress.Lanes, lane)
		t.progress.Total += lane.Size
	}
	t.lastAt = t.started
	return t
}

// lane finds id, adding it when the installation reports a package the
// plan did not list.
func (t *tracker) lane(id string, create bool) *Lane {
	if index, ok := t.index[id]; ok {
		return &t.progress.Lanes[index]
	}
	if !create {
		return nil
	}
	label := t.labels[id]
	if label == "" {
		label = id
	}
	t.index[id] = len(t.progress.Lanes)
	t.progress.Lanes = append(t.progress.Lanes, Lane{ID: id, Label: label})
	return &t.progress.Lanes[len(t.progress.Lanes)-1]
}

func (t *tracker) totals() {
	t.progress.Total, t.progress.Done = 0, 0
	for _, lane := range t.progress.Lanes {
		t.progress.Total += lane.Size
		t.progress.Done += lane.Done
	}
}

func (t *tracker) observer() *install.Observer {
	return &install.Observer{
		Package: func(id string, size int64) {
			t.mu.Lock()
			defer t.mu.Unlock()
			lane := t.lane(id, true)
			lane.Size, lane.Done = size, 0
			t.totals()
		},
		Bytes: func(id string, done int64) {
			t.mu.Lock()
			defer t.mu.Unlock()
			if lane := t.lane(id, false); lane != nil {
				lane.Done = min(done, lane.Size)
				t.totals()
			}
		},
		Files: func(id string, count int) {
			t.mu.Lock()
			defer t.mu.Unlock()
			if lane := t.lane(id, false); lane != nil {
				lane.FileCount = count
			}
		},
		File: func(id, path string, size int64, kept bool) {
			t.mu.Lock()
			defer t.mu.Unlock()
			lane := t.lane(id, false)
			if lane == nil {
				return // the installer's own layers, not a package
			}
			lane.Files++
			kind := byte('+')
			if kept {
				kind, lane.Kept = '=', lane.Kept+1
			}
			if size == 0 {
				size = lane.Size
			}
			t.progress.Log = append(t.progress.Log, LogLine{At: time.Since(t.started), Kind: kind, Path: path, Size: size})
			if len(t.progress.Log) > logLimit {
				t.progress.Log = t.progress.Log[len(t.progress.Log)-logLimit:]
			}
		},
		Wait: func(ctx context.Context) error {
			t.mu.Lock()
			if !t.wanted {
				t.mu.Unlock()
				return nil
			}
			t.progress.Holding = true
			resume := t.resume
			t.mu.Unlock()
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-resume:
			}
			t.mu.Lock()
			t.progress.Holding = false
			t.lastAt, t.lastDone = time.Now(), t.progress.Done
			t.mu.Unlock()
			return nil
		},
	}
}

// pause asks for a pause (true) or ends it (false). The installation
// holds at the next package boundary.
func (t *tracker) pause(on bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if on == t.wanted {
		return
	}
	t.wanted, t.progress.Paused = on, on
	if !on {
		close(t.resume)
		t.resume = make(chan struct{})
	}
}

// tick measures the speed and returns a snapshot.
func (t *tracker) tick(now time.Time) Progress {
	t.mu.Lock()
	defer t.mu.Unlock()
	if elapsed := now.Sub(t.lastAt).Seconds(); elapsed >= 0.25 {
		current := float64(t.progress.Done-t.lastDone) / elapsed
		if t.progress.Holding {
			current = 0
		}
		if t.progress.Speed == 0 {
			t.progress.Speed = current
		} else {
			t.progress.Speed = 0.7*t.progress.Speed + 0.3*current
		}
		t.lastAt, t.lastDone = now, t.progress.Done
	}
	t.progress.ETA = 0
	if t.progress.Speed > 1024 {
		t.progress.ETA = time.Duration(float64(t.progress.Total-t.progress.Done) / t.progress.Speed * float64(time.Second))
	}
	return snapshot(t.progress)
}

// track runs work with the tracker's observer and streams snapshots until
// it ends; the last one is Finished, or carries the error.
func track(ctx context.Context, t *tracker, control <-chan bool, work func(ctx context.Context) ([]Text, error)) <-chan Progress {
	// One slot of buffer lets the last snapshot be left behind when nobody
	// reads any more.
	out := make(chan Progress, 1)
	done := make(chan struct{})
	var warnings []Text
	var failure error
	go func() {
		defer close(done)
		warnings, failure = work(install.WithObserver(ctx, t.observer()))
	}()
	go func() {
		defer close(out)
		ticker := time.NewTicker(120 * time.Millisecond)
		defer ticker.Stop()
		// Snapshots go out while the work runs; once cancelled, only the
		// last one, after the rollback.
	running:
		for {
			select {
			case on := <-control:
				t.pause(on)
			case <-ticker.C:
				select {
				case out <- t.tick(time.Now()):
				case <-ctx.Done():
					break running
				}
			case <-done:
				break running
			}
		}
		<-done
		last := t.tick(time.Now())
		last.Holding, last.Paused, last.Speed, last.ETA = false, false, 0, 0
		last.Notes = warnings
		for _, warning := range warnings {
			last.Warnings = append(last.Warnings, warning.String())
		}
		if failure != nil {
			last.Err = failure
		} else {
			last.Finished = true
			for index := range last.Lanes {
				last.Lanes[index].Done = last.Lanes[index].Size
			}
			last.Done = last.Total
		}
		// The last snapshot always arrives, even after a cancel, so the
		// interface learns how the installation ended: a stale one still
		// in the buffer makes room for it.
		select {
		case <-out:
		default:
		}
		out <- last
	}()
	return out
}

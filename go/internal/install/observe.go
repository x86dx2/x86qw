package install

import "context"

// Observer hears how an installation advances. Its functions run on the
// installing goroutine and any of them may be nil.
type Observer struct {
	// Package starts one package: a component ID, the core data package
	// or "client" for ezQuake, with the size of its download.
	Package func(id string, size int64)
	// Bytes reports how much of the current download attempt for id has
	// arrived; a package already in the cache reports its size at once.
	Bytes func(id string, done int64)
	// Files announces how many files id is about to place.
	Files func(id string, count int)
	// File reports one file of id placed in the installation; kept means a
	// personal or changed copy was preserved instead.
	File func(id, path string, size int64, kept bool)
	// Wait runs before each package and may block: that is how an
	// installation pauses, always between packages.
	Wait func(ctx context.Context) error
}

type observerKey struct{}

// WithObserver returns a context whose installations report to observer.
func WithObserver(ctx context.Context, observer *Observer) context.Context {
	return context.WithValue(ctx, observerKey{}, observer)
}

// observing is the context's observer, or one that hears nothing.
func observing(ctx context.Context) *Observer {
	if observer, ok := ctx.Value(observerKey{}).(*Observer); ok && observer != nil {
		return observer
	}
	return &Observer{}
}

func (o *Observer) begin(ctx context.Context, id string, size int64) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if o.Wait != nil {
		if err := o.Wait(ctx); err != nil {
			return err
		}
	}
	if o.Package != nil {
		o.Package(id, size)
	}
	return nil
}

func (o *Observer) bytes(id string) func(int64) {
	if o.Bytes == nil {
		return nil
	}
	return func(done int64) { o.Bytes(id, done) }
}

func (o *Observer) files(id string, count int) {
	if o.Files != nil {
		o.Files(id, count)
	}
}

func (o *Observer) file(id, path string, size int64, kept bool) {
	if o.File != nil {
		o.File(id, path, size, kept)
	}
}

// observer is the transaction's observer, never nil.
func (t *Transaction) observer() *Observer {
	if t.observe == nil {
		return &Observer{}
	}
	return t.observe
}

package play

import (
	"os"
	"testing"
	"time"
)

// TestMain lets tests start this test binary as a long-running child
// (X86QW_TEST_CHILD=sleep) without depending on system tools.
func TestMain(m *testing.M) {
	if os.Getenv("X86QW_TEST_CHILD") == "sleep" {
		time.Sleep(2 * time.Minute)
		os.Exit(0)
	}
	os.Exit(m.Run())
}

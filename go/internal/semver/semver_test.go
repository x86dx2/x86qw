package semver

import "testing"

func TestPrecedenceMatchesSemVerSpec(t *testing.T) {
	ordered := []string{
		"1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
		"1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.16", "1.47.0+x86qw.19", "2.0.0",
	}
	for index := 0; index+1 < len(ordered); index++ {
		result, err := CompareStrings(ordered[index], ordered[index+1])
		if err != nil || result != -1 {
			t.Fatalf("%s < %s: got %d, %v", ordered[index], ordered[index+1], result, err)
		}
	}
	if result, _ := CompareStrings("1.0.0+a", "1.0.0+b"); result != 0 {
		t.Fatal("build metadata must not affect precedence")
	}
}

func TestRejectsNonStrictVersions(t *testing.T) {
	for _, value := range []string{"v1.0.0", "01.0.0", "1.0", "1.0.0-01", "1.0.0-", "1.0.0+", "1.47+x86qw.19"} {
		if Valid(value) {
			t.Fatalf("%q aceito", value)
		}
	}
	if got, _ := Parse("1.2.3-rc.1+b.5"); got.String() != "1.2.3-rc.1+b.5" {
		t.Fatalf("round-trip %s", got)
	}
}

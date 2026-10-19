package zdeflate

import (
	"archive/zip"
	"bytes"
	"compress/flate"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// cacheDir is where the Python installer caches published packages.
func cacheDir() string {
	if dir := os.Getenv("X86QW_COMPONENT_CACHE"); dir != "" {
		return dir
	}
	output, err := exec.Command("getconf", "DARWIN_USER_CACHE_DIR").Output()
	if err != nil {
		return ""
	}
	return filepath.Join(strings.TrimSpace(string(output)), "x86qw", "components")
}

func roundTrip(t *testing.T, raw []byte, label string) {
	t.Helper()
	plain, err := io.ReadAll(flate.NewReader(bytes.NewReader(raw)))
	if err != nil {
		t.Fatalf("%s: inflate: %v", label, err)
	}
	if again := Compress(plain); !bytes.Equal(again, raw) {
		t.Fatalf("%s: deflate diverge (%d bytes vs %d publicados)", label, len(again), len(raw))
	}
}

// TestPublishedPackagesRedeflate re-deflates every member of the published
// component packages and requires the exact stored bytes back.
func TestPublishedPackagesRedeflate(t *testing.T) {
	dir := cacheDir()
	paths, _ := filepath.Glob(filepath.Join(dir, "*.zip"))
	if len(paths) == 0 {
		t.Skip("cache de pacotes publicados indisponível")
	}
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil || (info.Size() > 64<<20 && os.Getenv("X86QW_DEFLATE_LARGE") != "1") {
			continue
		}
		reader, err := zip.OpenReader(path)
		if err != nil {
			t.Fatal(err)
		}
		for _, file := range reader.File {
			if file.Method != zip.Deflate {
				continue
			}
			source, err := file.OpenRaw()
			if err != nil {
				t.Fatal(err)
			}
			raw, _ := io.ReadAll(source)
			roundTrip(t, raw, filepath.Base(path)+":"+file.Name)
		}
		reader.Close()
	}
}

func syntheticInputs() map[string][]byte {
	random := rand.New(rand.NewSource(86))
	noise := func(n int) []byte {
		buffer := make([]byte, n)
		random.Read(buffer)
		return buffer
	}
	inputs := map[string][]byte{
		"empty": {}, "one": {'a'}, "two": []byte("ab"), "three": []byte("abc"),
		"zeros-258": make([]byte, 258), "zeros-1M": make([]byte, 1<<20),
		"random-100k": noise(100000),
	}
	for _, size := range []int{32767, 32768, 32769, 65535, 65536, 65537} {
		inputs["random-"+strconv.Itoa(size)] = noise(size)
	}
	var text bytes.Buffer
	for i := 0; text.Len() < 300000; i++ {
		text.WriteString("bind " + strconv.Itoa(i%97) + " \"impulse " + strconv.Itoa(i%13) + "\"\n")
	}
	inputs["text-300k"] = text.Bytes()
	// Repeats just past TOO_FAR (4096) exercise the length-3 discard.
	var far bytes.Buffer
	for far.Len() < 200000 {
		far.Write(noise(4097))
		far.WriteString("xyz")
	}
	inputs["too-far"] = far.Bytes()
	// A Fibonacci-skewed alphabet forces Huffman length overflow.
	var skew bytes.Buffer
	a, b := 1, 1
	for symbol := 0; symbol < 30; symbol++ {
		skew.Write(bytes.Repeat([]byte{byte(symbol*7 + 1)}, a))
		a, b = b, a+b
	}
	inputs["fibonacci"] = skew.Bytes()[:min(skew.Len(), 2_000_000)]
	mixed := append(append(noise(50000), make([]byte, 70000)...), text.Bytes()[:90000]...)
	inputs["mixed"] = mixed
	return inputs
}

// Digests of Compress output, produced by CPython's zlib 1.2.12.
var syntheticDigests = map[string]string{
	"empty":        "9b4fb24edd6d1d8830e272398263cdbf026b97392cc35387b991dc0248a628f9",
	"fibonacci":    "37cc424d95947581da0935551e806e6389245e38a9cd2d6050da7f876a6c1a5f",
	"mixed":        "c6add373309b750248f4cdb391067ca0a6136eb70c304b83d42499d08d71a7c1",
	"one":          "59fced9a2ff83dabdd7055cf3abd6c12b802e6397b6fba5b8251cdf95d2bc991",
	"random-100k":  "41626d48eb1f2ce35e99ebbe0c6789496a3d22712ae4c88fae30f9db029a21b3",
	"random-32767": "95164ed94647c202291f47107e89d815c0837eddf489d5334c0f4ebad573f563",
	"random-32768": "752e16350ee2e2bdbf0d08684ace0f2240842c449897b823eb0dfb176a0a75bc",
	"random-32769": "f359ce82843b7fac83af8b43d1a20c31c1b805281470508f58a1108e519761e2",
	"random-65535": "fecb1bcce59514964a144522d979fcb7a4e9c60b22e8878e95278bd64cad5f19",
	"random-65536": "8c3be6b706bd2e53adc4c0d492bc9d08ac81afe91bf6610c25b05b29048a4104",
	"random-65537": "245044c836f15bc5fc3a1c0b8d89472a2cb2cf51ab4029cee9a98989fc5ddc9a",
	"text-300k":    "739a2cde66d4da0c6cfbf785891fc339bf8717cd41c29633617df25340299385",
	"three":        "7ce573d21753aedbe7d6d1f5893e3f1265a35b9b51e42a154174a4ce9602ae39",
	"too-far":      "e91053eea803378400f83f448a108eda7363d00053f2bbadced4ebb10e3b4ac4",
	"two":          "c56b491f83efe08007a87b2d5846a56ca06f4a4dfc421ec8d2dcbfb944579f5e",
	"zeros-1M":     "8d22b086820b0896bdcb33cf965ebc275cb0b5f0b4c44a364aa4144c015f9f7b",
	"zeros-258":    "62e6de3c069bae19b9fd840f7685c6a3bf6afd32b9cd1504b425089253db4a6f",
}

func TestSyntheticInputs(t *testing.T) {
	python, _ := exec.LookPath("python3.13")
	parity := os.Getenv("X86QW_CONTENT_PARITY") == "1" && python != ""
	for name, input := range syntheticInputs() {
		output := Compress(input)
		plain, err := io.ReadAll(flate.NewReader(bytes.NewReader(output)))
		if err != nil || !bytes.Equal(plain, input) {
			t.Fatalf("%s: saída não reinfla para a entrada", name)
		}
		sum := sha256.Sum256(output)
		digest := hex.EncodeToString(sum[:])
		if expected, ok := syntheticDigests[name]; ok && expected != digest {
			t.Errorf("%s: digest %s, esperado %s", name, digest, expected)
		}
		if parity {
			command := exec.Command(python, "-I", "-c",
				"import sys,zlib,hashlib;d=sys.stdin.buffer.read();c=zlib.compressobj(6,zlib.DEFLATED,-15);print(hashlib.sha256(c.compress(d)+c.flush()).hexdigest())")
			command.Stdin = bytes.NewReader(input)
			reference, err := command.Output()
			if err != nil {
				t.Fatal(err)
			}
			if strings.TrimSpace(string(reference)) != digest {
				t.Errorf("%s: diverge do zlib do CPython", name)
			}
			t.Logf("%q: %q,", name, digest)
		}
	}
}

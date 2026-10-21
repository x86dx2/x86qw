package setup

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestTextsSpeakBothLanguages(t *testing.T) {
	text := Say("disk.short", Size(1_234_567_890))
	if pt, en := text.In(LangPT), text.In(LangEN); pt != "A instalação precisa de 1,23 GB." || en != "The installation needs 1.23 GB." {
		t.Fatalf("%q · %q", pt, en)
	}
	if got := Say("warn.of", Say("warn.choices"), Raw("disco cheio")).In(LangEN); got != "choices: disco cheio" {
		t.Fatalf("%q", got)
	}
	for key, pair := range messages {
		if pair[0] == "" || pair[1] == "" {
			t.Errorf("%s sem tradução", key)
		}
	}
}

func TestValidateNamesTheField(t *testing.T) {
	cases := []struct {
		field string
		edit  func(*Options)
	}{
		{"nick", func(o *Options) { o.Nick = "" }},
		{"nick", func(o *Options) { o.Nick = `a"b` }},
		{"server.name", func(o *Options) { o.Server.Name = "nome; quit" }},
		{"server.password", func(o *Options) { o.Server.Spectators = `x\y` }},
		{"server.rcon", func(o *Options) { o.Server.Rcon = "a\nb" }},
		{"server.slots", func(o *Options) { o.Server.Slots = 40 }},
		{"server.qtv", func(o *Options) { o.Server.QTVPort = 80 }},
		{"server.ports", func(o *Options) { o.Server.BasePort = 70000 }},
	}
	for _, c := range cases {
		o := Defaults(Both, "/x")
		o.Nick = "x86dx2"
		c.edit(&o)
		err := o.Validate()
		if FieldOf(err) != c.field || Explain(err, LangEN) == Explain(err, LangPT) {
			t.Errorf("%s: %v (%q)", c.field, err, FieldOf(err))
		}
	}
}

// TestClientConfigQuotesForTheConsole: ezQuake reads quotes literally, so
// a nick with accents goes as it is, not as Go escapes.
func TestClientConfigQuotesForTheConsole(t *testing.T) {
	o := Defaults(Play, "/x")
	o.Nick = "joão"
	config := ClientConfig(o, 1)
	if !strings.Contains(config, `name                     "joão"`) || strings.Contains(config, `\u`) {
		t.Fatalf("%s", config)
	}
}

func TestReadRecipeLeavesTheFolderToTheCaller(t *testing.T) {
	o := Defaults(Host, "/de/outra/maquina")
	payload, _ := o.RecipeJSON()
	got, err := ReadRecipe(strings.NewReader(string(payload)))
	if err != nil || got.Target != "" || got.Server.Rcon == "" || got.Intent != Host {
		t.Fatalf("%+v %v", got, err)
	}
	for _, bad := range []string{"{", `{"format": 2}`, `{"format": 1, "Surprise": true}`} {
		if _, err := ReadRecipe(strings.NewReader(bad)); err == nil {
			t.Fatalf("%s deveria ser recusado", bad)
		}
	}
}

func TestRecipeFileCarriesNoSecretAndComesBack(t *testing.T) {
	o := Defaults(Both, "/de/outra/maquina")
	o.Nick, o.Server.Password, o.Server.Spectators = "x86dx2", "jogador", "olho"
	payload, err := o.RecipeJSON()
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{o.Server.Rcon, "jogador", "olho"} {
		if strings.Contains(string(payload), secret) {
			t.Fatalf("a receita carrega %q", secret)
		}
	}
	path := filepath.Join(t.TempDir(), "receita.json")
	os.WriteFile(path, payload, 0o644)
	got, err := ParseRecipe([]string{"--recipe", path, "--nick", "outro", "--target", "/aqui"})
	if err != nil {
		t.Fatal(err)
	}
	if got.Intent != Both || got.Nick != "outro" || got.Target != "/aqui" || got.Server.Rcon == "" || got.Server.Password != "" ||
		got.Server.Name != o.Server.Name || len(got.Server.Instances) != 2 {
		t.Fatalf("%+v", got)
	}
}

// TestStartServerWritesTheRcon: an installation recorded as play-only
// still starts its server, with the rcon file the command reads.
func TestStartServerWritesTheRcon(t *testing.T) {
	// A short folder: MVDSV refuses a deep one, as a macOS temp dir is.
	base := "/tmp"
	if runtime.GOOS == "windows" {
		base = ""
	}
	target, err := os.MkdirTemp(base, "x86qw-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(target) })
	var ran []string
	r := &Real{Run: func(args []string) error { ran = args; return nil }}
	options := Defaults(Play, target)
	if err := r.StartServer(context.Background(), target, options); err != nil {
		t.Fatal(err)
	}
	if rcon, err := os.ReadFile(filepath.Join(target, filepath.FromSlash(RconFile))); err != nil || strings.TrimSpace(string(rcon)) != options.Server.Rcon {
		t.Fatalf("rcon: %q %v", rcon, err)
	}
	if len(ran) == 0 || ran[0] != "host" {
		t.Fatalf("%v", ran)
	}
	options.Server.Name = ""
	if err := r.StartService(context.Background(), target, "qtv", options); FieldOf(err) != "server.name" {
		t.Fatalf("serviço sem nome: %v", err)
	}
}

func TestDemoAppliesOnlyWhatIsAdded(t *testing.T) {
	d := &Demo{ExistingInstall: true, Speed: 50}
	existing, _ := d.Existing(context.Background(), "/x")
	options := existing.Options
	if lanes := d.lanes(options); len(lanes) != 0 {
		t.Fatalf("nada novo, nada a baixar: %+v", lanes)
	}
	options.Content = "complete"
	added := d.lanes(options)
	options.Upgrade = true
	if upgraded := d.lanes(options); len(added) == 0 || len(upgraded) != len(added)+len(existing.Updates) {
		t.Fatalf("adições %d, com atualizações %d", len(added), len(upgraded))
	}
	options.Server.Local, options.Server.QWFWDPort = true, 0
	if err := d.StartServer(context.Background(), "/x", options); err != nil {
		t.Fatal(err)
	}
	stack, _ := d.Stack(context.Background(), "/x")
	if len(stack.Processes) != 3 || stack.Processes[0].Address != "127.0.0.1" {
		t.Fatalf("o servidor da demo segue as escolhas: %+v", stack)
	}
}

#!/usr/bin/env python3
"""Guard PreToolUse dos tipos x86qw-* no Claude Code.

Uso no frontmatter dos agentes:
  python3 -I -B "$CLAUDE_PROJECT_DIR/.claude/hooks/x86qw_guard.py" <modo> || exit 2

Modos: readonly, test, maker, orchestrator. O payload JSON do hook chega em
stdin. Saída 0 sem texto permite; saída 2 com motivo em stderr nega. Entrada
inesperada nega (fail-closed). Somente stdlib; não grava arquivos nem usa rede.

readonly aplica allowlist de shell. test, maker e orchestrator aplicam
denylist contra ações acidentais; isso não é sandbox e não substitui revisão
independente, Git e evidência nativa (docs/agent-tooling/claude/POLICY.md).
A análise não preserva aspas: pontuação entre aspas pode gerar negações
conservadoras, nunca liberar mais do que a forma sem aspas.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

MODES = frozenset({"readonly", "test", "maker", "orchestrator"})
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SPAWN_TOOLS = frozenset({"Agent", "Task"})
CHILD_TYPES = frozenset({
    "x86qw-explorer", "x86qw-architect", "x86qw-inventory-auditor",
    "x86qw-maker", "x86qw-critical-maker", "x86qw-test-designer",
    "x86qw-test-runner", "x86qw-contract-checker", "x86qw-security-checker",
    "x86qw-release-checker", "x86qw-finalizer",
})
SPAWN_OVERRIDES = ("model", "effort", "isolation")
MAKER_PROTECTED = (".claude/hooks/", ".claude/agents/", ".claude/settings.json",
                   ".claude/settings.local.json")
ROOT_PROTECTED = (".claude/", ".codex/", ".grok/", "AGENTS.md", "CLAUDE.md")


class Denied(Exception):
    """Motivo de negação devolvido ao Claude Code."""


# --------------------------------------------------------------------------
# Análise do comando de shell

SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", "(", ")"})
WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})
DUP_REDIRECTS = frozenset({">&", "<&"})
READ_REDIRECTS = frozenset({"<", "<<", "<<<"})
PUNCTUATION = frozenset("();<>|&")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
EXPANSION = frozenset("$`{}*?[]")


@dataclass
class Parsed:
    commands: list = field(default_factory=list)
    writes: list = field(default_factory=list)


def substitutions(command):
    """Conteúdo de $(...) e `...`, inclusive entre aspas duplas."""
    found, i = [], 0
    while i < len(command):
        if command.startswith("$(", i):
            depth, j = 1, i + 2
            while j < len(command) and depth:
                depth += {"(": 1, ")": -1}.get(command[j], 0)
                j += 1
            found.append(command[i + 2:j - 1] if depth == 0 else command[i + 2:])
            i = j
        elif command[i] == "`":
            j = command.find("`", i + 1)
            found.append(command[i + 1:] if j < 0 else command[i + 1:j])
            i = len(command) if j < 0 else j + 1
        else:
            i += 1
    return [inner for inner in found if inner.strip()]


def parse(command, *, strict):
    if not isinstance(command, str) or not command.strip():
        raise Denied("comando ausente ou vazio")
    if strict:
        for marker in ("\n", "\r", "`", "$(", "<(", ">("):
            if marker in command:
                raise Denied(f"construção de shell não permitida em leitura: {marker!r}")
    else:
        command = re.sub(r"[\r\n`]", " ; ", command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise Denied(f"comando não analisável ({exc})") from None
    parsed, current, i = Parsed(), [], 0
    while i < len(tokens):
        token = tokens[i]
        if not token or not set(token) <= PUNCTUATION:
            current.append(token)
            i += 1
            continue
        if token in SEPARATORS:
            if strict and token in ("(", ")"):
                raise Denied("subshell não permitido em leitura")
            if current:
                parsed.commands.append(current)
                current = []
            i += 1
            continue
        if token not in WRITE_REDIRECTS | DUP_REDIRECTS | READ_REDIRECTS:
            raise Denied(f"operador de shell não reconhecido: {token!r}")
        if i + 1 >= len(tokens):
            raise Denied("redirecionamento sem alvo")
        target = tokens[i + 1]
        if current and current[-1].isdigit():
            current.pop()
        if token in WRITE_REDIRECTS and target != "/dev/null":
            parsed.writes.append(target)
        elif token == ">&" and not target.isdigit():
            parsed.writes.append(target)
        i += 2
    if current:
        parsed.commands.append(current)
    if not parsed.commands:
        raise Denied("nenhum comando reconhecido")
    if strict and parsed.writes:
        raise Denied("redirecionamento de escrita não permitido em leitura")
    return parsed


def split_assignments(command):
    i = 0
    while i < len(command) and ASSIGNMENT.match(command[i]):
        i += 1
    return command[:i], command[i:]


def short_flag(args, letter):
    return any(a.startswith("-") and not a.startswith("--") and letter in a[1:]
               for a in args)


def first_positional(args):
    return next((a for a in args if not a.startswith("-")), None)


# --------------------------------------------------------------------------
# Contexto de caminhos (maker e orchestrator)

@dataclass
class Context:
    cwd: str
    toplevel: str | None
    main_checkout: str | None
    tmp_roots: tuple

    @classmethod
    def from_payload(cls, payload):
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise Denied("payload sem cwd absoluto")
        toplevel = main = None
        try:
            out = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--path-format=absolute",
                 "--show-toplevel", "--git-common-dir"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.splitlines()
            toplevel = os.path.realpath(out[0])
            common = os.path.realpath(out[1])
            if os.path.basename(common) == ".git":
                main = os.path.dirname(common)
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
        roots = {"/tmp", "/private/tmp", "/var/folders", "/private/var/folders"}
        if os.environ.get("TMPDIR"):
            roots.add(os.environ["TMPDIR"])
        return cls(os.path.realpath(cwd), toplevel, main,
                   tuple(sorted({os.path.realpath(r) for r in roots})))

    def resolve(self, path):
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        return os.path.realpath(path)

    @staticmethod
    def inside(path, root):
        return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

    def relative(self, path):
        if self.toplevel and self.inside(path, self.toplevel):
            return os.path.relpath(path, self.toplevel)
        return None

    def isolated(self):
        return bool(self.toplevel and self.main_checkout
                    and self.toplevel != self.main_checkout)


def protected(rel, prefixes):
    return rel is not None and any(
        rel == p.rstrip("/") or (p.endswith("/") and rel.startswith(p))
        for p in prefixes)


def require_inside(ctx, path, what):
    target = ctx.resolve(path)
    if target == "/dev/null" or any(ctx.inside(target, r) for r in ctx.tmp_roots):
        return
    if not (ctx.toplevel and ctx.inside(target, ctx.toplevel)):
        raise Denied(f"{what} fora do worktree do Maker: {path}")
    if protected(ctx.relative(target), MAKER_PROTECTED):
        raise Denied(f"{what} em configuração protegida do harness: {path}")


def root_target(ctx, path, what):
    if protected(ctx.relative(ctx.resolve(path)), ROOT_PROTECTED):
        raise Denied(f"{what} em configuração de harness ou AGENTS.md exige ação humana: {path}")


# --------------------------------------------------------------------------
# Regras de leitura por programa

def check_find(args):
    for a in args:
        if a in FIND_EXEC + FIND_WRITE:
            raise Denied(f"find {a} não permitido em leitura")


def check_sort(args):
    for a in args:
        if a.startswith("--output") or (a.startswith("-") and not a.startswith("--") and "o" in a[1:]):
            raise Denied("sort -o/--output não permitido em leitura")


def check_uniq(args):
    if len([a for a in args if not a.startswith("-")]) > 1:
        raise Denied("uniq com arquivo de saída não permitido em leitura")


def check_rg(args):
    if any(a == "--pre" or a.startswith("--pre=") for a in args):
        raise Denied("rg --pre executa programas; não permitido em leitura")


def check_tree(args):
    if any(a.startswith("-o") for a in args):
        raise Denied("tree -o grava arquivo; não permitido em leitura")


def check_file(args):
    if any(a in ("-C", "--compile") for a in args):
        raise Denied("file -C grava arquivo; não permitido em leitura")


def check_date(args):
    for a in args:
        if a == "-s" or a.startswith("--set") or not a.startswith(("-", "+")):
            raise Denied("date só pode formatar; alterar o relógio não é permitido")


SED_PRINT = re.compile(r"(\d+|\$)(,(\d+|\$))?p")


def check_sed(args):
    if len(args) < 2 or args[0] not in ("-n", "-nE", "-En", "-nr", "-rn"):
        raise Denied("em leitura, use sed somente como: sed -n 'N,Mp' arquivo")
    if not all(SED_PRINT.fullmatch(part) for part in args[1].split(";")):
        raise Denied("em leitura, sed só imprime intervalos de linhas")
    if any(a.startswith("-") for a in args[2:]):
        raise Denied("opções adicionais de sed não permitidas em leitura")


def listing_only(sub, args, forbidden, value_options, list_flags, allow_create=False):
    listing = any(a in list_flags for a in args)
    expect_value = False
    for a in args:
        if expect_value:
            expect_value = False
            continue
        name = a.split("=", 1)[0]
        short = a.startswith("-") and not a.startswith("--")
        if name in forbidden or (short and any("-" + c in forbidden for c in a[1:])):
            raise Denied(f"git {sub} {a} altera referências")
        if a in value_options:
            expect_value = True
        elif not a.startswith("-") and not listing and not allow_create:
            raise Denied(f"git {sub} com nome posicional cria referência; use a forma de listagem")


BRANCH_FORBIDDEN = frozenset({"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C",
                              "--copy", "-f", "--force", "-u", "--set-upstream-to",
                              "--unset-upstream", "--edit-description", "-t", "--track",
                              "--no-track", "--create-reflog"})
BRANCH_VALUES = frozenset({"--contains", "--no-contains", "--merged", "--no-merged",
                           "--points-at", "--format", "--sort"})
TAG_FORBIDDEN = frozenset({"-a", "--annotate", "-s", "--sign", "-u", "--local-user", "-f",
                           "--force", "-d", "--delete", "-m", "--message", "-F", "--file",
                           "-e", "--edit", "--cleanup", "--create-reflog"})


def git_branch(args):
    listing_only("branch", args, BRANCH_FORBIDDEN, BRANCH_VALUES, {"-l", "--list"})


def git_tag(args):
    listing_only("tag", args, TAG_FORBIDDEN, BRANCH_VALUES, {"-l", "--list", "-v", "--verify"})


def git_remote(args):
    if args in ([], ["-v"], ["--verbose"]) or (args and args[0] == "get-url"):
        return
    raise Denied("git remote só pode listar ou consultar URL")


def git_worktree(args):
    if not args or args[0] != "list":
        raise Denied("git worktree só pode listar neste papel")


def git_stash(args):
    if not args or args[0] not in ("list", "show"):
        raise Denied("git stash só pode listar ou mostrar neste papel")


CONFIG_READ = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list",
                         "-l", "--get-color", "--get-colorbool"})
CONFIG_WRITE = frozenset({"--add", "--unset", "--unset-all", "--replace-all",
                          "--rename-section", "--remove-section", "--edit", "-e"})


def git_config(args):
    if args and args[0] in ("get", "list"):
        return
    if any(a in CONFIG_READ for a in args) and not any(a in CONFIG_WRITE for a in args):
        return
    raise Denied("git config só pode ler neste papel")


def git_reflog(args):
    if args and args[0] in ("expire", "delete", "drop"):
        raise Denied(f"git reflog {args[0]} altera o repositório")


def git_notes(args):
    if args and args[0] not in ("list", "show"):
        raise Denied("git notes só pode listar ou mostrar")


GIT_READ = frozenset({
    "status", "log", "show", "diff", "rev-parse", "rev-list", "ls-files", "ls-tree",
    "cat-file", "merge-base", "blame", "annotate", "grep", "shortlog", "describe",
    "name-rev", "for-each-ref", "show-ref", "show-branch", "count-objects",
    "check-ignore", "check-attr", "var", "version", "whatchanged", "range-diff",
    "cherry", "ls-remote", "verify-commit", "verify-tag", "diff-tree", "diff-index",
    "diff-files",
})
GIT_LISTING = {"branch": git_branch, "tag": git_tag, "remote": git_remote,
               "worktree": git_worktree, "stash": git_stash, "config": git_config,
               "reflog": git_reflog, "notes": git_notes}
GIT_FLAGS = frozenset({"--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs",
                       "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
                       "--no-replace-objects", "--bare"})
GIT_VALUE_FLAGS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})


def split_git(args):
    paths, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("--version", "--help", "-h"):
            return "version", [], paths
        if a in GIT_FLAGS:
            i += 1
            continue
        if a in GIT_VALUE_FLAGS:
            if i + 1 >= len(args):
                raise Denied(f"git {a} sem valor")
            paths.append(args[i + 1])
            i += 2
            continue
        name, sep, value = a.partition("=")
        if sep and name in ("--git-dir", "--work-tree", "--namespace"):
            paths.append(value)
            i += 1
            continue
        if a.startswith("-"):
            raise Denied(f"opção global do git não permitida: {a}")
        return a, args[i + 1:], paths
    raise Denied("git sem subcomando")


def git_common(sub, args):
    for a in args:
        if a.startswith("--output") or a == "--no-verify":
            raise Denied(f"git {sub} {a} não permitido")
    if sub == "grep" and any(a.startswith(("-O", "--open-files-in-pager")) for a in args):
        raise Denied("git grep -O executa pager; não permitido")
    if sub == "commit" and short_flag(args, "n"):
        raise Denied("git commit -n ignora hooks; não permitido")


def git_readonly(args):
    sub, rest, _ = split_git(args)
    git_common(sub, rest)
    if sub in GIT_READ:
        return
    if sub not in GIT_LISTING:
        raise Denied(f"git {sub} não permitido em leitura")
    GIT_LISTING[sub](rest)


MAKER_GIT_WRITE = frozenset({"add", "rm", "mv", "restore", "commit", "switch", "checkout",
                             "stash", "cherry-pick", "revert", "am", "apply", "clean"})


def git_maker(args, ctx):
    sub, rest, paths = split_git(args)
    for p in paths:
        require_inside(ctx, p, "git -C/--git-dir/--work-tree")
    git_common(sub, rest)
    if sub in GIT_READ or sub in MAKER_GIT_WRITE:
        return
    if sub == "reset":
        if any(a in ("--hard", "--merge", "--keep") for a in rest):
            raise Denied("git reset destrutivo não permitido ao Maker")
        return
    if sub == "branch":
        listing_only("branch", rest, BRANCH_FORBIDDEN - {"-t", "--track", "--no-track"},
                     BRANCH_VALUES, {"-l", "--list"}, allow_create=True)
        return
    if sub in GIT_LISTING:
        GIT_LISTING[sub](rest)
        return
    raise Denied(f"git {sub} não permitido ao Maker; push, merge, rebase e fetch exigem ação humana")


ROOT_GIT_WRITE = frozenset({"add", "commit", "fetch", "worktree"})


def git_root(args):
    sub, rest, _ = split_git(args)
    git_common(sub, rest)
    if sub in GIT_READ or sub in ROOT_GIT_WRITE:
        return
    if sub == "branch":
        listing_only("branch", rest, frozenset({"-m", "-M", "--move", "-c", "-C", "--copy",
                                                "-u", "--set-upstream-to", "--unset-upstream"}),
                     BRANCH_VALUES, {"-l", "--list"}, allow_create=True)
        return
    if sub in GIT_LISTING:
        GIT_LISTING[sub](rest)
        return
    raise Denied(f"git {sub} exige ação humana após autorização explícita (rode com ! no prompt)")


AIMEM_READ = frozenset({"status", "search", "read-page", "workstreams", "handoffs",
                        "workstream-search", "audit-contamination", "checkpoints", "help"})


def check_aimem(args, allowed):
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--help", "-h", "--version", "-V"):
            return
        if a in ("--data-dir", "--config"):
            i += 2
            continue
        if a.startswith(("--data-dir=", "--config=")):
            i += 1
            continue
        if a.startswith("-"):
            raise Denied(f"opção global do ai-memory não permitida: {a}")
        if a in allowed:
            return
        raise Denied(f"ai-memory {a} não permitido neste papel")
    raise Denied("ai-memory sem subcomando")


GH_READ = frozenset({("run", "list"), ("run", "view"), ("pr", "view"), ("pr", "list"),
                     ("pr", "checks"), ("pr", "diff"), ("pr", "status"), ("issue", "view"),
                     ("issue", "list"), ("release", "view"), ("release", "list"),
                     ("repo", "view"), ("workflow", "list"), ("workflow", "view"),
                     ("auth", "status"), ("cache", "list")})


def check_gh(args):
    if args and args[0] in ("--version", "--help", "-h", "version"):
        return
    if args and args[0] == "api":
        rest, i = args[1:], 0
        while i < len(rest):
            a = rest[i]
            if a in ("-X", "--method"):
                if i + 1 >= len(rest) or rest[i + 1].upper() != "GET":
                    raise Denied("gh api só com método GET")
                i += 2
                continue
            if (a.startswith("--method=") and a[9:].upper() != "GET") or \
                    (a.startswith("-X") and len(a) > 2 and a[2:].upper() != "GET"):
                raise Denied("gh api só com método GET")
            if a.startswith(("-f", "-F", "--field", "--raw-field", "--input")):
                raise Denied("gh api com campos ou corpo implica escrita")
            i += 1
        return
    if len(args) < 2 or (args[0], args[1]) not in GH_READ:
        raise Denied("gh: somente consultas de leitura são permitidas")


FREE = None
FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")
FIND_WRITE = ("-delete", "-fprint", "-fprint0", "-fprintf", "-fls")
READONLY_PROGRAMS = {
    "ls": FREE, "cat": FREE, "head": FREE, "tail": FREE, "wc": FREE, "grep": FREE,
    "egrep": FREE, "fgrep": FREE, "cut": FREE, "tr": FREE, "diff": FREE, "cmp": FREE,
    "comm": FREE, "stat": FREE, "du": FREE, "df": FREE, "pwd": FREE, "echo": FREE,
    "printf": FREE, "true": FREE, "false": FREE, "test": FREE, "[": FREE,
    "basename": FREE, "dirname": FREE, "realpath": FREE, "readlink": FREE,
    "which": FREE, "type": FREE, "uname": FREE, "sw_vers": FREE, "shasum": FREE,
    "sha256sum": FREE, "sha1sum": FREE, "sha512sum": FREE, "md5": FREE,
    "md5sum": FREE, "cksum": FREE, "b2sum": FREE, "od": FREE, "hexdump": FREE,
    "strings": FREE, "jq": FREE, "column": FREE, "nl": FREE, "paste": FREE,
    "fold": FREE, "expand": FREE, "unexpand": FREE, "rev": FREE, "tac": FREE,
    "cd": FREE, "id": FREE, "whoami": FREE,
    "find": check_find, "sort": check_sort, "uniq": check_uniq, "rg": check_rg,
    "tree": check_tree, "file": check_file, "date": check_date, "sed": check_sed,
    "git": git_readonly, "gh": check_gh,
    "ai-memory": lambda args: check_aimem(args, AIMEM_READ),
}
SAFE_ENV = frozenset({"NO_COLOR", "LC_ALL", "LANG", "TZ", "COLUMNS", "TERM",
                      "GIT_OPTIONAL_LOCKS", "GIT_PAGER", "PAGER"})


def readonly_command(command):
    assignments, argv = split_assignments(command)
    for a in assignments:
        name, value = a.split("=", 1)
        if name not in SAFE_ENV or (name in ("GIT_PAGER", "PAGER") and value not in ("", "cat")):
            raise Denied(f"variável de ambiente não permitida em leitura: {name}")
    if not argv:
        raise Denied("atribuição sem comando")
    program = argv[0]
    if "/" in program or program.startswith("~") or EXPANSION & set(program):
        raise Denied(f"programa por caminho ou expansão não permitido em leitura: {program}")
    if program not in READONLY_PROGRAMS:
        raise Denied(f"{program} não está na allowlist de leitura")
    check = READONLY_PROGRAMS[program]
    if check is not None:
        check(argv[1:])


# --------------------------------------------------------------------------
# Denylist (test, maker, orchestrator)

DENY_ALWAYS = frozenset({
    "sudo", "su", "doas", "ssh", "scp", "sftp", "ftp", "telnet", "nc", "ncat", "socat",
    "curl", "wget", "rsync", "open", "osascript", "launchctl", "crontab", "brew", "glab",
    "security", "defaults", "eval", "source", ".", "npx", "pipx", "exec", "alias",
    "dd", "shred",
})
WRAPPERS = frozenset({"command", "builtin", "time", "nice", "nohup", "timeout",
                      "caffeinate", "stdbuf", "xargs", "arch"})
DECLARE = frozenset({"export", "declare", "typeset", "local", "readonly"})
FILE_MUTATORS = frozenset({
    "rm", "rmdir", "mv", "cp", "ln", "chmod", "chown", "chgrp", "chflags", "xattr",
    "truncate", "tee", "install", "touch", "mkfifo", "patch", "unzip", "tar", "ditto",
    "mkdir",
})
INSTALLERS = {
    "pip": {"install", "uninstall", "download"}, "pip3": {"install", "uninstall", "download"},
    "npm": {"install", "i", "ci", "add", "publish", "update", "uninstall", "remove", "link", "exec"},
    "pnpm": {"install", "i", "add", "publish", "update", "remove", "dlx", "exec"},
    "yarn": {"add", "install", "publish", "upgrade", "remove", "dlx"},
    "cargo": {"install", "publish", "add", "remove", "update"},
    "gem": {"install", "push", "uninstall"}, "go": {"get", "install"},
    "uv": {"add", "remove", "sync", "pip", "tool"},
}
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
IN_PLACE = re.compile(r"^(-[a-zA-Z]*i|-I|--in-place)")
RISKY_ENV = re.compile(r"(GIT_\w+|LD_\w+|DYLD_\w+)")
SAFE_GIT_ENV = frozenset({"GIT_PAGER", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"})


def check_env(name):
    if RISKY_ENV.fullmatch(name) and name not in SAFE_GIT_ENV:
        raise Denied(f"variável {name} redireciona git ou o carregador; não permitido")


def check_find_denylist(args, mode, ctx):
    if mode == "test" and any(a in FIND_EXEC + FIND_WRITE for a in args):
        raise Denied("find com -exec/-delete não permitido ao Test Runner")
    i = 0
    while i < len(args):
        if args[i] in FIND_EXEC:
            j = i + 1
            while j < len(args) and args[j] not in (";", "+"):
                j += 1
            inner = [a for a in args[i + 1:j] if a != "{}"]
            if inner:
                denylist_command(inner, mode, ctx)
            i = j
        i += 1
    if mode == "maker" and any(a in FIND_WRITE for a in args):
        for a in args:
            if a.startswith(("-", "(", "!")):
                break
            require_inside(ctx, a, "find")


def denylist_command(command, mode, ctx):
    assignments, argv = split_assignments(command)
    for a in assignments:
        check_env(a.split("=", 1)[0])
    if not argv:
        return
    if EXPANSION & set(argv[0]):
        raise Denied(f"programa por expansão não permitido: {argv[0]}")
    program, args = os.path.basename(argv[0]), argv[1:]
    if program in DENY_ALWAYS:
        raise Denied(f"{program} não permitido a agentes x86qw (rede, privilégio, execução indireta ou escrita bruta)")
    if program == "env":
        if args and args[0].startswith("-"):
            raise Denied("env com opções não permitido")
        denylist_command(args, mode, ctx)
        return
    if program in WRAPPERS:
        rest = list(args)
        while rest and (rest[0].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", rest[0])):
            rest.pop(0)
        if rest:
            denylist_command(rest, mode, ctx)
        return
    if program in DECLARE:
        for a in args:
            check_env(a.split("=", 1)[0])
        return
    if program == "gh":
        check_gh(args)
        return
    if program == "ai-memory":
        check_aimem(args, AIMEM_READ | ({"write-page"} if mode == "orchestrator" else set()))
        return
    sub = first_positional(args)
    if program in INSTALLERS and sub in INSTALLERS[program]:
        raise Denied(f"{program} {sub} instala ou publica dependências; exige assignment e ação humana")
    if program == "go" and sub == "mod":
        rest = args[args.index("mod") + 1:]
        if not rest or rest[0] not in ("verify", "why", "graph"):
            raise Denied("go mod altera go.mod/go.sum ou usa rede; exige assignment e lock próprios")
    if program.startswith("python") and "-m" in args:
        module = args[args.index("-m") + 1:args.index("-m") + 2]
        if module in (["pip"], ["ensurepip"]) and {"install", "uninstall", "download"} & set(args):
            raise Denied("pip install exige assignment e ação humana")
    if program in SHELLS and "-c" in args:
        position = args.index("-c") + 1
        if position >= len(args):
            raise Denied(f"{program} -c sem comando")
        check_bash(mode, args[position], ctx)
        return
    if program == "find":
        check_find_denylist(args, mode, ctx)
        return
    in_place = program in ("sed", "perl", "ruby") and any(IN_PLACE.match(a) for a in args)
    if mode == "test":
        if program in FILE_MUTATORS - {"mkdir"} or in_place:
            raise Denied(f"{program} altera arquivos; o Test Runner não modifica o código revisado")
        if program == "git":
            git_readonly(args)
        return
    if program == "git":
        if mode == "maker":
            git_maker(args, ctx)
        else:
            git_root(args)
        return
    if program == "cd":
        if mode == "maker":
            targets = [a for a in args if a not in ("-P", "-L")]
            if len(targets) != 1 or targets[0] == "-":
                raise Denied("Maker usa cd somente com um diretório dentro do worktree")
            require_inside(ctx, targets[0], "cd")
        return
    if program in FILE_MUTATORS or in_place:
        for a in args:
            if a.startswith("-"):
                continue
            if mode == "maker":
                require_inside(ctx, a, program)
            else:
                root_target(ctx, a, program)


def check_bash(mode, command, ctx):
    if mode == "readonly" or (mode == "maker" and not ctx.isolated()):
        for item in parse(command, strict=True).commands:
            readonly_command(item)
        return
    for inner in substitutions(command):
        check_bash(mode, inner, ctx)
    parsed = parse(command, strict=False)
    for target in parsed.writes:
        if mode == "test":
            raise Denied("o Test Runner não grava por redirecionamento; use stdout")
        if mode == "maker":
            require_inside(ctx, target, "redirecionamento")
        else:
            root_target(ctx, target, "redirecionamento")
    for item in parsed.commands:
        denylist_command(item, mode, ctx)


# --------------------------------------------------------------------------
# Decisão

def check_spawn(tool_input):
    child = tool_input.get("subagent_type")
    if child not in CHILD_TYPES:
        raise Denied(f"tipo de subagente não permitido: {child!r}; use somente folhas x86qw-*")
    for key in SPAWN_OVERRIDES:
        if tool_input.get(key) not in (None, ""):
            raise Denied(f"override de {key} no spawn é proibido; o tipo configurado é a autoridade")
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or "assignment_id" not in prompt:
        raise Denied("spawn sem assignment_id no prompt")


def check_edit(mode, tool_input, ctx):
    path = tool_input.get("file_path", tool_input.get("notebook_path"))
    if not isinstance(path, str) or not path:
        raise Denied("edição sem caminho")
    if mode == "orchestrator":
        root_target(ctx, path, "edição")
        return
    if mode != "maker":
        raise Denied("este papel não edita arquivos")
    if not ctx.isolated():
        raise Denied("Maker fora de worktree isolado; edição no checkout principal é proibida")
    require_inside(ctx, path, "edição")


def decide(mode, payload, ctx=None):
    if mode not in MODES:
        raise Denied(f"modo do guard inválido: {mode!r}")
    if not isinstance(payload, dict):
        raise Denied("payload não é objeto JSON")
    event = payload.get("hook_event_name", "PreToolUse")
    if event != "PreToolUse":
        raise Denied(f"evento inesperado: {event!r}")
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        raise Denied("payload sem tool_name/tool_input válidos")
    if tool in SPAWN_TOOLS:
        if mode != "orchestrator":
            raise Denied("folhas não criam subagentes")
        check_spawn(tool_input)
        return
    if tool not in EDIT_TOOLS and tool != "Bash":
        raise Denied(f"ferramenta inesperada para o guard: {tool}")
    if ctx is None and mode in ("maker", "orchestrator"):
        ctx = Context.from_payload(payload)
    if tool in EDIT_TOOLS:
        check_edit(mode, tool_input, ctx)
        return
    check_bash(mode, tool_input.get("command"), ctx)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if len(argv) == 1 else ""
    try:
        decide(mode, json.loads(sys.stdin.read()))
    except Denied as exc:
        print(f"x86qw-guard[{mode or '?'}]: negado: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # fail-closed
        print(f"x86qw-guard[{mode or '?'}]: erro interno, negado por segurança: {exc!r}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

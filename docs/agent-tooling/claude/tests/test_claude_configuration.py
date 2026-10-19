"""Contratos estáticos da configuração Claude Code do x86QW.

Run: python3 -B -m unittest discover -s docs/agent-tooling/claude/tests -v
Sem rede, subprocessos, instalações ou escrita. A qualificação runtime é separada
(docs/agent-tooling/claude/QUALIFICATION.md).
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
DOCS = ROOT / "docs/agent-tooling/claude"
AGENTS_DIR = ROOT / ".claude/agents"
GUARD_PATH = ROOT / ".claude/hooks/x86qw_guard.py"
MEMORY_TOOLS = ["mcp__ai-memory__memory_query", "mcp__ai-memory__memory_read_page",
                "mcp__ai-memory__memory_status"]
READ_TOOLS = ["Read", "Grep", "Glob", "Bash"] + MEMORY_TOOLS
MAKER_TOOLS = ["Read", "Grep", "Glob", "Bash", "Edit", "Write"] + MEMORY_TOOLS
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
COMMON_HEADING = "## Regras comuns das folhas x86qw-* (Claude Code)"


def hooks_block(mode):
    return [
        "hooks:",
        "  PreToolUse:",
        '    - matcher: "^(Bash|Edit|Write|MultiEdit|NotebookEdit|Agent|Task)$"',
        "      hooks:",
        "        - type: command",
        "          command: 'python3 -I -B \"$CLAUDE_PROJECT_DIR/.claude/hooks/x86qw_guard.py\" "
        + mode + " || exit 2'",
    ]


def read_agent(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    head, _, body = text[4:].partition("\n---\n")
    lines = head.split("\n")
    split = lines.index("hooks:")
    fields = dict(line.split(": ", 1) for line in lines[:split])
    return fields, lines[split:], body


def load_guard():
    spec = importlib.util.spec_from_file_location("x86qw_guard", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARD = load_guard()
ROUTING = json.loads((DOCS / "MODEL-ROUTING.json").read_text(encoding="utf-8"))


class AgentFiles(unittest.TestCase):
    def test_agent_set_matches_routing(self):
        names = {p.stem for p in AGENTS_DIR.glob("*.md")}
        self.assertEqual(names, set(ROUTING["roles"]) | set(ROUTING["top_level"]))
        self.assertEqual(set(ROUTING["roles"]), set(GUARD.CHILD_TYPES))
        self.assertEqual(ROUTING["leaf_memory_tools"], MEMORY_TOOLS)
        self.assertEqual(ROUTING["max_children"], 4)

    def test_leaf_frontmatter(self):
        for name, row in ROUTING["roles"].items():
            fields, hooks, body = read_agent(AGENTS_DIR / f"{name}.md")
            expected = {"name", "description", "model", "effort", "tools"}
            if row["isolation"] == "worktree":
                expected.add("isolation")
                self.assertEqual(fields["isolation"], "worktree", name)
            self.assertEqual(set(fields), expected, name)
            self.assertEqual(fields["name"], name)
            self.assertEqual((fields["model"], fields["effort"]), (row["model"], row["effort"]), name)
            self.assertNotIn(": ", fields["description"], name)
            self.assertIn("assignment_id", fields["description"], name)
            tools = fields["tools"].split(", ")
            self.assertEqual(tools, MAKER_TOOLS if row["guard"] == "maker" else READ_TOOLS, name)
            self.assertFalse({"Agent", "Task"} & set(tools), name)
            self.assertEqual(hooks, hooks_block(row["guard"]), name)
            self.assertTrue(row["leaf"])

    def test_top_level_frontmatter(self):
        for name, row in ROUTING["top_level"].items():
            fields, hooks, body = read_agent(AGENTS_DIR / f"{name}.md")
            self.assertEqual(set(fields), {"name", "description", "model", "effort",
                                           "permissionMode", "tools"}, name)
            self.assertEqual((fields["model"], fields["effort"], fields["permissionMode"]),
                             (row["model"], row["effort"], row["permission_mode"]), name)
            self.assertIn("nunca use como subagente", fields["description"])
            self.assertEqual(hooks, hooks_block(row["guard"]), name)
            self.assertFalse(row["spawnable"])
            self.assertIn("PLAN_DEVIATION", body)
        orchestrator = read_agent(AGENTS_DIR / "x86qw-orchestrator.md")[0]["tools"].split(", ")
        self.assertIn("Agent", orchestrator)
        self.assertFalse({"WebFetch", "WebSearch"} & set(orchestrator))
        self.assertTrue(all(t.startswith("mcp__ai-memory__") for t in orchestrator if t.startswith("mcp__")))
        reviewer = read_agent(AGENTS_DIR / "x86qw-external-reviewer.md")[0]["tools"].split(", ")
        self.assertEqual(reviewer, READ_TOOLS)

    def test_common_leaf_rules_are_identical(self):
        sections = set()
        for name in ROUTING["roles"]:
            body = read_agent(AGENTS_DIR / f"{name}.md")[2]
            self.assertEqual(body.count(COMMON_HEADING), 1, name)
            sections.add(body.split(COMMON_HEADING, 1)[1])
        self.assertEqual(len(sections), 1)
        common = sections.pop()
        for phrase in ("não cria subagentes", "memory_query, memory_read_page e memory_status",
                       "HOLD externo", "NOT_OBSERVABLE", "BLOCKED"):
            self.assertIn(phrase, common)


class Guard(unittest.TestCase):
    def setUp(self):
        self.worktree = GUARD.Context("/w/wt", "/w/wt", "/w/main", ("/private/tmp", "/tmp"))
        self.main = GUARD.Context("/w/main", "/w/main", "/w/main", ("/private/tmp", "/tmp"))

    def verdict(self, mode, tool, tool_input, ctx=None):
        try:
            GUARD.decide(mode, {"tool_name": tool, "tool_input": tool_input}, ctx)
        except GUARD.Denied:
            return False
        return True

    def bash(self, mode, command, ctx=None):
        return self.verdict(mode, "Bash", {"command": command}, ctx)

    def test_readonly_allows_reading(self):
        for command in ("git log --oneline -5 2>&1 | head", "grep 'a|b' file.txt",
                        "git status >/dev/null 2>&1", "git branch -a --contains HEAD",
                        "sed -n '1,20p' AGENTS.md", "ai-memory search 'G0'",
                        "gh pr view 12", "git -C /w/wt diff main...HEAD --stat",
                        "find . -name '*.md' | wc -l", "NO_COLOR=1 git show HEAD:AGENTS.md"):
            self.assertTrue(self.bash("readonly", command), command)

    def test_readonly_denies_writing(self):
        for command in ("touch /tmp/canary", "cat f > /tmp/out", "echo $(touch x)",
                        "git diff --output=x HEAD", "git branch newname", "git -c alias.x=!rm x",
                        "/bin/rm -rf x", "find . -exec rm {} \\;", "sed -i '' s/a/b/ f",
                        "python3 -c 'open(\"x\",\"w\")'", "git commit -m x", "git push",
                        "ai-memory write-page --path x", "gh api -X POST repos/x",
                        "ls\nrm -rf x", "(cd /tmp && rm x)", "xargs rm", "env", "tee x",
                        "GIT_DIR=/w/main/.git git log", "git stash", "git config user.name x"):
            self.assertFalse(self.bash("readonly", command), command)

    def test_test_runner(self):
        self.assertTrue(self.bash("test", "python3 -B -m unittest discover -s tests -v"))
        self.assertTrue(self.bash("test", "go test ./..."))
        for command in ("rm -rf build", "git push origin main", 'echo "$(git push)"',
                        "bash -c 'git commit -m x'", "pytest > out.txt", "curl https://x",
                        "pip install x", "go mod tidy", "sed -i s/a/b/ f", "find . -delete"):
            self.assertFalse(self.bash("test", command), command)

    def test_maker_in_worktree(self):
        ok = ("git switch -c go/g1/x 9fe34a3", "rm -rf build/out", "git commit -m 'feat: x'",
              "go test ./... > /tmp/go-test.log", "cd /w/wt/sub && git status")
        for command in ok:
            self.assertTrue(self.bash("maker", command, self.worktree), command)
        denied = ("git push", "cd /w/main && git status", "rm -rf /w/main/src",
                  "echo x > /w/main/f", "GIT_DIR=/w/main/.git git commit -m x",
                  "env GIT_WORK_TREE=/w/main git add .", "nice -n 5 git push",
                  "git commit -nm skip", "git${IFS}push", "git merge main", "git rebase main",
                  "git reset --hard HEAD~1", "git fetch", "rm -rf .claude/hooks",
                  "cd ..", "find /w/main -delete", "git -C /w/main status")
        for command in denied:
            self.assertFalse(self.bash("maker", command, self.worktree), command)

    def test_maker_outside_worktree_falls_back_to_readonly(self):
        self.assertTrue(self.bash("maker", "git status", self.main))
        self.assertFalse(self.bash("maker", "touch f", self.main))
        self.assertFalse(self.verdict("maker", "Edit", {"file_path": "/w/main/a.go"}, self.main))

    def test_maker_edits(self):
        self.assertTrue(self.verdict("maker", "Edit", {"file_path": "/w/wt/src/a.go"}, self.worktree))
        self.assertTrue(self.verdict("maker", "Write", {"file_path": "src/new.go"}, self.worktree))
        for path in ("/w/main/src/a.go", "/w/wt/.claude/hooks/x86qw_guard.py",
                     "/w/wt/.claude/agents/x86qw-maker.md", "../main/x"):
            self.assertFalse(self.verdict("maker", "Edit", {"file_path": path}, self.worktree), path)

    def test_orchestrator(self):
        spawn = {"subagent_type": "x86qw-explorer", "prompt": "assignment_id: A-1"}
        self.assertTrue(self.verdict("orchestrator", "Agent", spawn, self.main))
        for change in ({"subagent_type": "general-purpose"}, {"subagent_type": "x86qw-orchestrator"},
                       {"subagent_type": "x86qw-external-reviewer"}, {"model": "haiku"},
                       {"effort": "low"}, {"isolation": "worktree"}, {"prompt": "sem id"}):
            self.assertFalse(self.verdict("orchestrator", "Agent", {**spawn, **change}, self.main), change)
        self.assertTrue(self.bash("orchestrator", "git commit -m 'decisão'", self.main))
        self.assertTrue(self.bash("orchestrator", "git worktree list", self.main))
        for command in ("git push", "git merge go/g0/x", "git reset --hard", "git checkout -- .",
                        "rm -rf .codex", "sed -i '' s/a/b/ AGENTS.md", "gh pr merge 1",
                        "ai-memory delete-page --path x"):
            self.assertFalse(self.bash("orchestrator", command, self.main), command)
        self.assertTrue(self.verdict("orchestrator", "Write", {"file_path": "/w/main/docs/x.md"}, self.main))
        for path in ("/w/main/AGENTS.md", "/w/main/CLAUDE.md", "/w/main/.grok/README.md",
                     "/w/main/.claude/hooks/x86qw_guard.py"):
            self.assertFalse(self.verdict("orchestrator", "Edit", {"file_path": path}, self.main), path)

    def test_leaves_cannot_spawn_or_edit(self):
        spawn = {"subagent_type": "x86qw-explorer", "prompt": "assignment_id: A"}
        for mode in ("readonly", "test", "maker"):
            self.assertFalse(self.verdict(mode, "Agent", spawn, self.worktree), mode)
            self.assertFalse(self.verdict(mode, "Task", spawn, self.worktree), mode)
        for mode in ("readonly", "test"):
            self.assertFalse(self.verdict(mode, "Write", {"file_path": "/tmp/x"}), mode)

    def test_fail_closed(self):
        cases = [(["readonly"], "não é json"), ([], json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})),
                 (["bogus"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})),
                 (["readonly"], json.dumps({"tool_name": "Bash", "tool_input": {}})),
                 (["readonly"], json.dumps({"tool_name": "Bash", "tool_input": {"command": 7}})),
                 (["readonly"], json.dumps({"tool_name": "Unknown", "tool_input": {}})),
                 (["readonly"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"},
                                            "hook_event_name": "PostToolUse"})),
                 (["maker"], json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))]
        for argv, stdin in cases:
            with mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(GUARD.main(argv), 2, (argv, stdin))
        ok = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})
        with mock.patch("sys.stdin", io.StringIO(ok)):
            self.assertEqual(GUARD.main(["readonly"]), 0)


class Repository(unittest.TestCase):
    def test_agents_md_routing(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for marker in ("<!-- x86qw-codex-routing:start -->", "<!-- x86qw-codex-routing:end -->",
                       "<!-- x86qw-orchestration:start -->", "<!-- x86qw-orchestration:end -->"):
            self.assertEqual(text.count(marker), 1, marker)
        managed = text.split("<!-- ai-memory:start -->", 1)[1].split("<!-- ai-memory:end -->", 1)[0]
        before = (ROOT / "docs/agent-tooling/codex/evidence/20260908T030435Z/AGENTS.before.md").read_text()
        self.assertEqual(managed, before.split("<!-- ai-memory:start -->", 1)[1].split("<!-- ai-memory:end -->", 1)[0])
        for phrase in ("| Claude Code |", ".claude/agents/", "docs/agent-tooling/claude/POLICY.md",
                       "claude --agent x86qw-orchestrator", "Quando o executor for Claude Code",
                       "Não crie `CLAUDE.md` na raiz"):
            self.assertIn(phrase, text)

    def test_project_settings_only_set_worktree_base(self):
        settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, {"worktree": {"baseRef": "head"}})

    def test_no_root_claude_md_shadowing_agents_md(self):
        claude_md = ROOT / "CLAUDE.md"
        if claude_md.exists():
            self.assertIn("@AGENTS.md", claude_md.read_text(encoding="utf-8"))

    def test_marker_and_gitignore(self):
        self.assertEqual(hashlib.sha256((ROOT / ".ai-memory.toml").read_bytes()).hexdigest(),
                         "905dd6480ea780e64ef626cb585f839ea1651e1a1d5942ae3eb194aca52f42cf")
        self.assertIn("/.claude/worktrees/", (ROOT / ".gitignore").read_text().splitlines())

    def test_files_are_clean_text(self):
        files = [p for p in (ROOT / ".claude").rglob("*")
                 if "worktrees" not in p.parts and p.name != "settings.local.json"]
        files += list(DOCS.rglob("*")) + [ROOT / "AGENTS.md", ROOT / ".gitignore"]
        for p in files:
            if not p.is_file() or p.suffix == ".pyc" or "__pycache__" in p.parts:
                continue
            self.assertFalse(p.is_symlink(), str(p))
            data = p.read_bytes()
            self.assertNotIn(b"\x00", data, str(p))
            self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"), str(p))
            for line in data.decode("utf-8").splitlines():
                self.assertEqual(line, line.rstrip(), str(p))

    def test_observations_do_not_claim_unrun_qualification(self):
        report = json.loads((DOCS / "CAPABILITY-REPORT.json").read_text(encoding="utf-8"))
        if report["runtime_qualification"] != "PASS":
            self.assertNotEqual(report["configuration_status"], "PASS")
        for row in report["agent_results"]:
            if not row["transcript_ref"]:
                self.assertEqual(row["observed_model"], "NOT_OBSERVABLE", row["role"])
                self.assertNotEqual(row["result_status"], "PASS", row["role"])
        self.assertFalse(report["g1_authorized"])
        self.assertIsNone(report["migration_baseline_sha"])
        self.assertTrue(report["g0_external_hold_remains"])


if __name__ == "__main__":
    unittest.main()

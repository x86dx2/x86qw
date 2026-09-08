"""Static contract checks. Runtime qualifications are deliberately separate.

Run: python3 -B -m unittest discover -s docs/agent-tooling/codex/tests -v
No network, subprocesses, installations, or writes are performed by this suite.
"""
import hashlib
import json
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[4]
DOCS = ROOT / "docs/agent-tooling/codex"
READ_TOOLS = {"memory_query", "memory_read_page", "memory_status"}
EXPECTED = {
    "explorer": ("gpt-5.6-sol", "medium", "read-only"),
    "architect": ("gpt-6-astra", "xhigh", "read-only"),
    "inventory-auditor": ("gpt-6-astra", "xhigh", "read-only"),
    "maker": ("gpt-6-astra", "high", "workspace-write"),
    "critical-maker": ("gpt-6-astra", "xhigh", "workspace-write"),
    "test-designer": ("gpt-6-astra", "high", "read-only"),
    "test-runner": ("gpt-5.6-sol", "low", "read-only"),
    "contract-checker": ("gpt-6-astra", "xhigh", "read-only"),
    "security-checker": ("gpt-6-astra", "xhigh", "read-only"),
    "release-checker": ("gpt-6-astra", "xhigh", "read-only"),
    "finalizer": ("gpt-6-astra", "xhigh", "read-only"),
}


class AgentConfiguration(unittest.TestCase):
    def test_required_configuration_exists(self):
        self.assertTrue((ROOT / ".codex/config.toml").is_file())

    def test_all_emitted_keys_exist_in_pinned_schema(self):
        schema = json.loads((DOCS / "evidence/20260908T030435Z/recognized-schema-keys.json").read_text())
        paths = list((ROOT / ".codex").rglob("*.toml"))
        paths += list((DOCS / "optional").glob("*.example"))
        self.assertEqual(len(paths), 13)
        for path in paths:
            d = tomllib.loads(path.read_text())
            metadata = set(schema["agent_metadata_keys"]) if "agents" in path.parts or path.suffix == ".example" else set()
            self.assertFalse(set(d) - set(schema["top_keys"]) - metadata, str(path))
            for key in ("agents", "features", "sandbox_workspace_write"):
                self.assertFalse(set(d.get(key, {})) - set(schema[key + "_keys"]), str(path))
            for server in d.get("mcp_servers", {}).values():
                self.assertFalse(set(server) - set(schema["mcp_keys"]), str(path))
                self.assertEqual(sum(k in server for k in ("command", "url")), 1, str(path))

    def test_routing_permissions_and_tool_surface(self):
        config = tomllib.loads((ROOT / ".codex/config.toml").read_text())
        routing = json.loads((DOCS / "MODEL-ROUTING.json").read_text())
        self.assertEqual((config["model"], config["model_reasoning_effort"]),
                         ("gpt-6-astra", "xhigh"))
        self.assertEqual(config["approvals_reviewer"], "user")
        self.assertEqual(config["approval_policy"], "on-request")
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertFalse(config["sandbox_workspace_write"]["network_access"])
        self.assertEqual(config["agents"]["max_concurrent_threads_per_session"], 4)
        paths = sorted((ROOT / ".codex/agents").glob("*.toml"))
        agents = [tomllib.loads(p.read_text()) for p in paths]
        self.assertEqual({a["name"] for a in agents}, {"x86qw-" + n for n in EXPECTED})
        self.assertEqual(len(agents), len(EXPECTED))
        for a in [config] + agents:
            self.assertNotIn("default_permissions", a)
            self.assertNotIn("permissions", a)
            self.assertNotIn("profiles", a)
            self.assertNotIn("profile", a)
            self.assertNotIn("model_provider", a)
            self.assertFalse(a["features"]["apps"])
            self.assertFalse(a["features"]["plugins"])
            self.assertFalse(a["features"]["browser_use"])
            self.assertFalse(a["features"]["computer_use"])
            self.assertFalse(a["mcp_servers"]["node_repl"]["enabled"])
            self.assertFalse(a["mcp_servers"]["computer-use"]["enabled"])
            self.assertEqual(a["approvals_reviewer"], "user")
            self.assertEqual(a["mcp_servers"]["ai-memory"]["default_tools_approval_mode"], "prompt")
            allowed = set(a["mcp_servers"]["ai-memory"]["enabled_tools"])
            self.assertEqual(allowed, READ_TOOLS)
            self.assertNotIn("writable_roots", a.get("sandbox_workspace_write", {}))
        for a in agents:
            name = a["name"]
            expected = EXPECTED[name.removeprefix("x86qw-")]
            self.assertEqual(tuple(a[k] for k in ("model", "model_reasoning_effort", "sandbox_mode")), expected)
            self.assertFalse(a["agents"]["enabled"])
            self.assertFalse(a["features"]["multi_agent_v2"])
            self.assertEqual(a["approval_policy"], "on-request" if expected[2] == "workspace-write" else "never")
            self.assertTrue(a["description"].strip())
            self.assertTrue(a["developer_instructions"].strip())
            for k in ("prompt_file", "default_capability_mode", "default_isolation", "mcpInheritance"):
                self.assertNotIn(k, a)
            row = routing["roles"][name]
            self.assertEqual((row["model"], row["effort"], row["sandbox"]), expected)

    def test_managed_block_preserved_and_routing_unique(self):
        text = (ROOT / "AGENTS.md").read_text()
        managed = text.split("<!-- ai-memory:start -->", 1)[1].split("<!-- ai-memory:end -->", 1)[0]
        before = (DOCS / "evidence/20260908T030435Z/AGENTS.before.md").read_text()
        self.assertEqual(managed, before.split("<!-- ai-memory:start -->", 1)[1].split("<!-- ai-memory:end -->", 1)[0])
        self.assertEqual(text.count("<!-- x86qw-codex-routing:start -->"), 1)
        self.assertEqual(text.count("<!-- x86qw-codex-routing:end -->"), 1)
        self.assertEqual(hashlib.sha256((ROOT / ".ai-memory.toml").read_bytes()).hexdigest(),
                         "905dd6480ea780e64ef626cb585f839ea1651e1a1d5942ae3eb194aca52f42cf")

    def test_untracked_files_are_clean_text(self):
        files = list((ROOT / ".codex").rglob("*")) + list(DOCS.rglob("*")) + [ROOT / "AGENTS.md"]
        for p in files:
            if not p.is_file() or p.suffix in {".patch", ".pyc"}:
                continue
            self.assertFalse(p.is_symlink(), str(p))
            data = p.read_bytes()
            self.assertNotIn(b"\x00", data, str(p))
            self.assertTrue(data.endswith(b"\n"), str(p))
            for line in data.decode().splitlines():
                self.assertEqual(line, line.rstrip(), str(p))

    def test_observations_do_not_claim_unrun_qualification(self):
        report = json.loads((DOCS / "CAPABILITY-REPORT.json").read_text())
        if report["runtime_qualification"] != "PASS":
            self.assertNotEqual(report["configuration_status"], "PASS")
        for row in report["agent_results"]:
            if not row["native_spawn_evidence_ref"]:
                self.assertEqual(row["observed_model"], "NOT_OBSERVABLE")
                self.assertEqual(row["observed_effort"], "NOT_OBSERVABLE")
                self.assertNotEqual(row["result_status"], "PASS")
        self.assertFalse(report["g1_authorized"])
        self.assertIsNone(report["migration_baseline_sha"])
        self.assertTrue(report["g0_external_hold_remains"])


if __name__ == "__main__":
    unittest.main()

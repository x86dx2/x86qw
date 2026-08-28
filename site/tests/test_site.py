from html import unescape
from html.parser import HTMLParser
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / "public"
PROJECT_ROOT = ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from x86qw_runtime.io.archive import (  # noqa: E402
    extract_archive,
    read_archive_members,
    scan_archive,
)
from maintenance.tools.validate_catalog import published_package_count  # noqa: E402


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.refs = []
        self.tags = []
        self.elements = []
        self.lang = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        self.elements.append((tag, attrs))
        self.lang = attrs.get("lang", self.lang)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.refs.append(attrs[key])


class InstallCommands(HTMLParser):
    def __init__(self):
        super().__init__()
        self.commands = {}
        self.copy_targets = []
        self._command_id = None
        self._command_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "code" and "data-install-command" in attrs:
            self._command_id = attrs.get("id")
            self._command_text = []
        if tag == "button" and "data-copy-install" in attrs:
            self.copy_targets.append(attrs.get("data-copy-target"))

    def handle_data(self, data):
        if self._command_id is not None:
            self._command_text.append(data)

    def handle_endtag(self, tag):
        if tag == "code" and self._command_id is not None:
            self.commands[self._command_id] = "".join(self._command_text).strip()
            self._command_id = None
            self._command_text = []


class SiteTests(unittest.TestCase):
    def parse(self, name):
        page = Page()
        page.feed((ROOT / name).read_text(encoding="utf-8"))
        return page

    def test_pages_are_semantic_and_local_references_exist(self):
        home = self.parse("index.html")
        self.assertEqual(home.lang, "pt-BR")
        self.assertEqual(home.tags.count("main"), 1)
        self.assertEqual(home.tags.count("h1"), 1)

        for name in ("index.html", "404.html"):
            page = self.parse(name)
            for ref in page.refs:
                if ref.startswith("#"):
                    self.assertIn(ref[1:], page.ids, f"{name}: {ref}")
                elif ref.startswith("/") and not ref.startswith("//"):
                    self.assertTrue((ROOT / ref.lstrip("/")).exists(), f"{name}: {ref}")

    def test_accessibility_and_catalog_contract_remain_explicit(self):
        css = (ROOT / "assets" / "site.css").read_text(encoding="utf-8")
        script = (ROOT / "assets" / "site.js").read_text(encoding="utf-8")
        home = (ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("class=\"skip-link\"", home)
        self.assertIn("aria-live=\"polite\"", home)
        self.assertIn("/api/v1/catalog.json", script)
        self.assertIn("catalog.project !== 'x86qw'", script)
        self.assertIn("item.component !== 'installer'", script)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', home)
        self.assertIn('<table class="platform-matrix">', home)
        self.assertNotIn('role="table"', home)

    def test_release_documentation_keeps_mac_local_boundary(self):
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        cloudflare = (PROJECT_ROOT / "site/docs/cloudflare.md").read_text(encoding="utf-8")

        self.assertIn("não possui Developer ID nem notarização comprovada", home)
        self.assertIn("esse suporte continua condicional", home)
        self.assertIn("workflows protegidos", cloudflare)
        self.assertIn("publicação remota só ocorre após autorização explícita", cloudflare)
        self.assertIn("npm ci && npm run deploy:dry-run", cloudflare)
        self.assertIn("account_id", cloudflare)
        self.assertRegex(cloudflare, r"não é uma\s+credencial")

    def test_qw_is_canonical_and_the_legacy_hostname_remains_an_alias(self):
        canonical_origin = "https://qw.x86.com.br"
        legacy_hostname = "x86qw.x86.com.br"
        wrangler = json.loads(
            (PROJECT_ROOT / "site/wrangler.jsonc").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"qw.x86.com.br", legacy_hostname},
            {route["pattern"] for route in wrangler["routes"]},
        )
        self.assertTrue(all(route.get("custom_domain") is True for route in wrangler["routes"]))

        manager_tree = ast.parse(
            (PROJECT_ROOT / "dist/installer/bin/manager.py").read_text(encoding="utf-8")
        )
        assignments = {
            target.id: ast.literal_eval(statement.value)
            for statement in manager_tree.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
            and target.id in {
                "CATALOG_URL",
                "TRUST_METADATA_URL",
                "TRUST_TARGET_URL",
                "PUBLIC_UNIX_BOOTSTRAP_COMMAND",
                "PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND",
            }
        }
        self.assertEqual(f"{canonical_origin}/api/v1/catalog.json", assignments["CATALOG_URL"])
        self.assertEqual(
            f"{canonical_origin}/api/v1/trust/metadata/",
            assignments["TRUST_METADATA_URL"],
        )
        self.assertEqual(
            f"{canonical_origin}/api/v1/trust/targets/",
            assignments["TRUST_TARGET_URL"],
        )
        self.assertIn(f"{canonical_origin}/install.sh", assignments["PUBLIC_UNIX_BOOTSTRAP_COMMAND"])
        self.assertIn(
            f"{canonical_origin}/install.ps1",
            assignments["PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND"],
        )

        home = (ROOT / "index.html").read_text(encoding="utf-8")
        robots = (ROOT / "robots.txt").read_text(encoding="utf-8")
        sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn(f'<link rel="canonical" href="{canonical_origin}/">', home)
        self.assertIn(f'<meta property="og:url" content="{canonical_origin}/">', home)
        self.assertIn(f"Sitemap: {canonical_origin}/sitemap.xml", robots)
        self.assertIn(f"<loc>{canonical_origin}/</loc>", sitemap)

    def test_public_product_facts_match_the_canonical_catalogs_and_documentation(self):
        product = json.loads((ROOT / "api/v1/product.json").read_text(encoding="utf-8"))
        packages = json.loads((ROOT / "api/v1/catalog.json").read_text(encoding="utf-8"))
        components = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        runtimes = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/runtimes.json").read_text(encoding="utf-8")
        )
        games = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/games.json").read_text(encoding="utf-8")
        )
        capabilities = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/capabilities.json").read_text(encoding="utf-8")
        )
        version = (PROJECT_ROOT / "dist/installer/VERSION").read_text(encoding="utf-8").strip()
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        manual = (PROJECT_ROOT / "dist/installer/docs/installer.md").read_text(encoding="utf-8")

        self.assertEqual(version, product["version"])
        self.assertEqual(published_package_count(packages), product["package_count"])
        self.assertLess(product["package_count"], len(packages["packages"]))
        self.assertTrue(
            any(item.get("component") == "installer" for item in packages["packages"]),
        )
        self.assertEqual(len(components["components"]), product["component_count"])
        self.assertEqual(capabilities["commands"], product["commands"])
        self.assertEqual(
            {entry["id"] for entry in runtimes["runtimes"]},
            {entry["id"] for entry in product["runtimes"]},
        )
        canonical_support = {
            (runtime["id"], platform["variant"]): platform["support"]
            for runtime in runtimes["runtimes"]
            for platform in runtime["platforms"]
        }
        public_support = {
            (runtime["id"], platform["variant"]): platform["support"]
            for runtime in product["runtimes"]
            for platform in runtime["platforms"]
        }
        self.assertEqual(canonical_support, public_support)
        self.assertEqual(
            "conditional", public_support[("ezquake-stable", "macos-universal")],
        )
        self.assertEqual(
            "preview", public_support[("ezquake-nightly", "macos-universal")],
        )
        self.assertEqual(
            {entry["id"] for entry in games["games"]},
            {entry["id"] for entry in product["games"]},
        )
        self.assertEqual({"mvdsv", "qtv", "qwfwd"}, {
            entry["id"] for entry in product["runtimes"]
            if entry["kind"] in {"server", "service"}
        })
        service_variants = {
            platform["variant"]
            for runtime in product["runtimes"]
            if runtime["kind"] in {"server", "service"}
            for platform in runtime["platforms"]
        }
        self.assertEqual(
            {"macos-arm64", "linux-amd64", "windows-x64"}, service_variants,
        )
        for document in (readme, manual):
            self.assertIn(version, document)
            self.assertIn(f"{product['component_count']} componentes", document)
        visible_home = re.sub(r"<[^>]+>", "", home)
        self.assertIn(version, visible_home)
        self.assertIn(f"{product['component_count']} componentes", visible_home)
        self.assertIn(f"{product['package_count']} pacotes", visible_home)
        self.assertNotRegex(home, r"data-(?:product-version|package-count|component-count)")
        for game in product["games"]:
            self.assertIn(game["label"], visible_home)
            self.assertIn(game["version"], visible_home)
        for command in product["commands"]:
            self.assertIn(f"`{command}`", readme)
        cli_help = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "dist/installer/bin/manager.py"), "--help"],
            check=True, capture_output=True, text=True,
        ).stdout
        for command in product["commands"]:
            self.assertIn(command, cli_help)

    def test_deploy_provenance_endpoints_are_static_json(self):
        truth = json.loads(
            (PROJECT_ROOT / "docs/post-1.0/release-truth-current.json").read_text(
                encoding="utf-8"
            )
        )
        version = json.loads((ROOT / "version").read_text(encoding="utf-8"))
        api_version = json.loads((ROOT / "api/v1/version").read_text(encoding="utf-8"))
        health = json.loads((ROOT / "api/v1/health").read_text(encoding="utf-8"))
        self.assertEqual(version, api_version)
        self.assertEqual(truth["snapshot_commit"], version["commit"])
        self.assertEqual("owner-only", version["release_audience"])
        self.assertFalse(version["external_public"])
        self.assertEqual(
            truth["authorities"]["deployment"]["tuf"]["catalog_sha256"],
            version["catalog_sha256"],
        )
        self.assertEqual("ok", health["status"])
        self.assertEqual(version["commit"], health["commit"])
        self.assertFalse(health["external_public"])

    def test_unix_install_command_restricts_https_consistently(self):
        manager_path = PROJECT_ROOT / "dist/installer/bin/manager.py"
        manager_tree = ast.parse(manager_path.read_text(encoding="utf-8"))
        command = next(
            ast.literal_eval(statement.value)
            for statement in manager_tree.body
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PUBLIC_UNIX_BOOTSTRAP_COMMAND"
                for target in statement.targets
            )
        )
        self.assertEqual(
            "curl -fsS https://qw.x86.com.br/install.sh | bash",
            command,
        )
        self.assertNotIn("umask 077", command)
        self.assertNotIn("head -c", command)
        self.assertNotIn('"$(curl', command)
        documents = (
            PROJECT_ROOT / "README.md",
            PROJECT_ROOT / "dist/installer/docs/installer.md",
        )
        for path in documents:
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                document = unescape(path.read_text(encoding="utf-8"))
                self.assertIn(command, document)
                self.assertNotIn('/bin/bash -c "$(curl', document)

        powershell_command = next(
            ast.literal_eval(statement.value)
            for statement in manager_tree.body
            if isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND"
                for target in statement.targets
            )
        )
        for fragment in (
            "System.Net.Http.HttpClient",
            "AllowAutoRedirect = $false",
            "Timeout = [TimeSpan]::FromSeconds(60)",
            "MaxResponseContentBufferSize = 262144",
            "ContentLength -gt 262144",
        ):
            self.assertIn(fragment, powershell_command)
        for path in documents:
            with self.subTest(path=path.relative_to(PROJECT_ROOT), shell="powershell"):
                self.assertIn(powershell_command, path.read_text(encoding="utf-8"))

    def test_home_copies_the_hardened_command_for_each_shell(self):
        commands = InstallCommands()
        commands.feed((ROOT / "index.html").read_text(encoding="utf-8"))

        manager_tree = ast.parse(
            (PROJECT_ROOT / "dist/installer/bin/manager.py").read_text(encoding="utf-8")
        )
        assignments = {
            target.id: ast.literal_eval(statement.value)
            for statement in manager_tree.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
            and target.id in {
                "PUBLIC_UNIX_BOOTSTRAP_COMMAND",
                "PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND",
            }
        }

        self.assertEqual(
            {
                "install-unix-command": assignments["PUBLIC_UNIX_BOOTSTRAP_COMMAND"],
                "install-windows-command": assignments["PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND"],
            },
            commands.commands,
        )
        self.assertEqual(
            {"install-unix-command", "install-windows-command"},
            set(commands.copy_targets),
        )

    def test_public_copy_explains_audience_and_prerequisites_plainly(self):
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("validada para uso do próprio mantenedor", home)
        self.assertIn("Python 3.10 ou mais recente", home)
        self.assertIn("ainda não oferece suporte público geral", home)
        self.assertNotIn("valida o bootstrap", home)
        self.assertNotIn("NO-GO", home)
        self.assertNotIn("portable-contract", home)

    def test_social_metadata_and_first_party_links_are_complete(self):
        page = self.parse("index.html")
        metas = {
            attrs.get("property") or attrs.get("name"): attrs.get("content")
            for tag, attrs in page.elements
            if tag == "meta" and (attrs.get("property") or attrs.get("name"))
        }
        links = {
            attrs.get("rel"): attrs.get("href")
            for tag, attrs in page.elements
            if tag == "link" and attrs.get("rel")
        }
        self.assertEqual("summary_large_image", metas["twitter:card"])
        self.assertEqual(metas["og:title"], metas["twitter:title"])
        self.assertEqual(metas["og:description"], metas["twitter:description"])
        self.assertEqual(metas["og:image"], metas["twitter:image"])
        self.assertEqual("1200", metas["og:image:width"])
        self.assertEqual("630", metas["og:image:height"])
        self.assertEqual(metas["og:image:alt"], metas["twitter:image:alt"])
        self.assertTrue(metas["og:image"].startswith("https://qw.x86.com.br/"))

        image = ROOT / metas["og:image"].removeprefix("https://qw.x86.com.br/")
        self.assertTrue(image.is_file())
        data = image.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
        self.assertEqual((1200, 630), struct.unpack(">II", data[16:24]))
        self.assertEqual("/assets/apple-touch-icon.png", links["apple-touch-icon"])

        home = (ROOT / "index.html").read_text(encoding="utf-8")
        for url in (
            "https://github.com/x86dx2/x86qw",
            "https://github.com/x86dx2/x86qw/releases",
            "https://github.com/x86dx2/x86qw/issues",
            "https://github.com/x86dx2/x86qw/security/policy",
        ):
            self.assertIn(f'href="{url}"', home)

    def test_static_delivery_declares_security_and_cache_policy(self):
        headers = (ROOT / "_headers").read_text(encoding="utf-8")
        for value in (
            "Content-Security-Policy:",
            "X-Content-Type-Options: nosniff",
            "Referrer-Policy: strict-origin-when-cross-origin",
            "Permissions-Policy:",
            "Strict-Transport-Security:",
            "frame-ancestors 'none'",
            "script-src 'self' 'unsafe-inline'",
            "/install.sh",
            "/install.ps1",
            "/api/v1/trust/metadata/*",
            "/api/v1/trust/targets/*",
            "/version",
            "/api/v1/version",
            "/api/v1/health",
            "max-age=31536000, immutable",
        ):
            self.assertIn(value, headers)

        rules = {}
        route = None
        for line in headers.splitlines():
            if line and not line.startswith(" "):
                route = line
                rules[route] = []
            elif route is not None and line.strip():
                rules[route].append(line.strip())
        self.assertFalse(any(value.startswith("Cache-Control:") for value in rules["/*"]))
        self.assertIn(
            "Cache-Control: public, max-age=604800, must-revalidate",
            rules["/assets/social-card.png"],
        )
        self.assertEqual(
            ["Cache-Control: public, max-age=31536000, immutable", "Access-Control-Allow-Origin: *"],
            rules["/api/v1/trust/targets/*"],
        )

        package = json.loads((PROJECT_ROOT / "site/package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            "node --test --test-concurrency=1 tests/*.test.mjs",
            package["scripts"]["test"],
        )
        workflow = (PROJECT_ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("npm test", workflow)

    def test_public_bootstrap_matches_the_registered_installer_bundle(self):
        catalog = json.loads((ROOT / "api/v1/catalog.json").read_text(encoding="utf-8"))
        installers = [item for item in catalog["packages"] if item.get("package") == "x86qw-installer"]
        current = [item for item in installers if item.get("current") is True]
        self.assertEqual(1, len(current))
        package = current[0]
        product = json.loads((ROOT / "api/v1/product.json").read_text(encoding="utf-8"))
        self.assertEqual(package["version"], product["version"])
        self.assertEqual(package["sha256"], product["installer"]["sha256"])
        self.assertEqual(f"x86QW Installer {package['version']}", package["release_title"])
        self.assertIn(
            f"github.com/x86dx2/x86qw/releases/download/x86qw-installer-{package['version']}/",
            package["urls"][0],
        )
        self.assertEqual(
            {
                path.parent.name
                for path in (PROJECT_ROOT / "dist/installer/packages").glob("*/*.zip")
                if not path.parent.is_symlink()
            },
            {item["version"] for item in installers},
        )
        for historical in installers:
            historical_bundle = ROOT.parents[1] / "dist" / historical["distribution_path"]
            self.assertEqual(historical["size"], historical_bundle.stat().st_size)
            self.assertEqual(historical["sha256"], hashlib.sha256(historical_bundle.read_bytes()).hexdigest())
        bundle = ROOT.parents[1] / "dist" / package["distribution_path"]
        self.assertEqual(package["size"], bundle.stat().st_size)
        self.assertEqual(package["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        prefix = f"x86qw-installer-{package['version']}"
        bundle_plan = scan_archive(bundle)
        names = bundle_plan.member_names
        bundle_members = read_archive_members(bundle_plan, (
            f"{prefix}/installer.json",
            f"{prefix}/VERSION",
            f"{prefix}/x86qw.pyz",
            f"{prefix}/_x86qw/installer.json",
        ))
        identity = json.loads(bundle_members[f"{prefix}/installer.json"])
        outer_version = bundle_members[f"{prefix}/VERSION"].decode()
        application = bundle_members[f"{prefix}/x86qw.pyz"]
        legacy_identity = json.loads(bundle_members[f"{prefix}/_x86qw/installer.json"])
        with tempfile.TemporaryDirectory() as temporary:
            extracted = Path(temporary) / "bundle"
            extract_archive(bundle_plan, extracted)
            legacy_entrypoint = extracted / prefix / "dist/installer/bin/manager.py"
            legacy_result = subprocess.run(
                [sys.executable, str(legacy_entrypoint), "--help"],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(
            {
                f"{prefix}/installer.json", f"{prefix}/x86qw.pyz",
                f"{prefix}/VERSION",
                f"{prefix}/LICENSE", f"{prefix}/NOTICE",
                f"{prefix}/x86qw.sh", f"{prefix}/x86qw.cmd",
                f"{prefix}/dist/installer/bin/manager.py",
                f"{prefix}/_x86qw/installer.json",
            },
            set(names),
        )
        application_plan = scan_archive(application, required_members=(
            "_x86qw/installer.json", "_x86qw/components.json",
        ))
        application_members = read_archive_members(application_plan, (
            "_x86qw/installer.json", "_x86qw/components.json",
        ))
        embedded_identity = json.loads(application_members["_x86qw/installer.json"])
        runtime = json.loads(application_members["_x86qw/components.json"])
        self.assertEqual(
            {"format": 1, "project": "x86qw", "version": package["version"]},
            identity,
        )
        self.assertEqual(identity, embedded_identity)
        self.assertEqual(package["version"] + "\n", outer_version)
        self.assertIn(f"x86QW {package['version']}", legacy_result.stdout)
        self.assertEqual(identity, legacy_identity)
        self.assertEqual(0, legacy_result.returncode, legacy_result.stderr)
        self.assertIn("usage: x86qw", legacy_result.stdout)
        self.assertLess(package["size"], 1024 * 1024)
        self.assertFalse(any(name.endswith((".pak", ".pk3", "qwprogs.dat")) for name in names))
        self.assertFalse(any("/dist/mods/" in name or "/maintenance/inventory/" in name for name in names))
        self.assertEqual("x86qw-runtime", runtime["project"])
        self.assertTrue(all("sources" not in component for component in runtime["components"]))
        self.assertTrue(all("project_sources" not in component for component in runtime["components"]))
        latest = ROOT.parents[1] / "dist" / "installer" / "packages" / "latest"
        self.assertTrue(latest.is_symlink())
        self.assertEqual(package["version"], os.readlink(latest))
        for name in ("install.sh", "install.ps1"):
            script = (ROOT / name).read_text(encoding="utf-8")
            canonical = (ROOT.parents[1] / "dist" / "installer" / "bin" / name).read_text(encoding="utf-8")
            self.assertEqual(canonical, script)
            self.assertIn(package["sha256"], script)
            self.assertIn(str(package["size"]), script)
            self.assertNotIn("__X86QW_INSTALLER_SHA256__", script)
        powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertNotRegex(powershell, r"(?m)^\s*exit(?:\s|$)")
        self.assertIn("$InstallerExitCode = $LASTEXITCODE", powershell)
        self.assertIn("$global:LASTEXITCODE = $InstallerExitCode", powershell)
        self.assertIn("-ErrorAction Continue", powershell)
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertRegex(home, re.escape('https://qw.x86.com.br/install.sh'))
        self.assertIn("data-copy-install", home)


if __name__ == "__main__":
    unittest.main()

from html.parser import HTMLParser
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1] / "public"
PROJECT_ROOT = ROOT.parents[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.refs = []
        self.tags = []
        self.lang = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        self.lang = attrs.get("lang", self.lang)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.refs.append(attrs[key])


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
        self.assertEqual(len(packages["packages"]), product["package_count"])
        self.assertEqual(len(components["components"]), product["component_count"])
        self.assertEqual(capabilities["commands"], product["commands"])
        self.assertEqual(
            {entry["id"] for entry in runtimes["runtimes"]},
            {entry["id"] for entry in product["runtimes"]},
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
        for document in (home, readme, manual):
            self.assertIn(version, document)
            self.assertIn(f"{product['component_count']} componentes", document)
        self.assertIn(f"{product['package_count']} pacotes", home)
        for command in product["commands"]:
            self.assertIn(f"`{command}`", readme)
        cli_help = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "dist/installer/bin/manager.py"), "--help"],
            check=True, capture_output=True, text=True,
        ).stdout
        for command in product["commands"]:
            self.assertIn(command, cli_help)

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
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            prefix = f"x86qw-installer-{package['version']}"
            identity = json.loads(archive.read(
                f"{prefix}/installer.json"
            ))
            outer_version = archive.read(f"{prefix}/VERSION").decode()
            application = archive.read(f"{prefix}/x86qw.pyz")
            legacy_identity = json.loads(archive.read(f"{prefix}/_x86qw/installer.json"))
            with tempfile.TemporaryDirectory() as temporary:
                archive.extractall(temporary)
                legacy_entrypoint = Path(temporary) / prefix / "dist/installer/bin/manager.py"
                legacy_result = subprocess.run(
                    [sys.executable, str(legacy_entrypoint), "--help"],
                    check=False, capture_output=True, text=True,
                )
        self.assertEqual(
            {
                f"{prefix}/installer.json", f"{prefix}/x86qw.pyz",
                f"{prefix}/VERSION",
                f"{prefix}/x86qw.sh", f"{prefix}/x86qw.cmd",
                f"{prefix}/dist/installer/bin/manager.py",
                f"{prefix}/_x86qw/installer.json",
            },
            set(names),
        )
        with zipfile.ZipFile(io.BytesIO(application)) as zipapp:
            embedded_identity = json.loads(zipapp.read("_x86qw/installer.json"))
            runtime = json.loads(zipapp.read("_x86qw/components.json"))
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
            self.assertNotIn("__X86QW_INSTALLER_SHA256__", script)
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertRegex(home, re.escape('https://x86qw.x86.com.br/install.sh'))
        self.assertIn("data-copy-install", home)


if __name__ == "__main__":
    unittest.main()

from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1] / "public"


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

    def test_public_bootstrap_matches_the_registered_installer_bundle(self):
        catalog = json.loads((ROOT / "api/v1/catalog.json").read_text(encoding="utf-8"))
        installers = [item for item in catalog["packages"] if item.get("package") == "x86qw-installer"]
        current = [item for item in installers if item.get("current") is True]
        self.assertEqual(1, len(current))
        package = current[0]
        self.assertEqual("x86QW Installer 0.1.0", package["release_title"])
        self.assertIn(
            "github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.1.0/",
            package["urls"][0],
        )
        self.assertEqual(
            ["0.1.0"],
            sorted((item["version"] for item in installers), key=lambda value: tuple(map(int, value.split(".")))),
        )
        for historical in installers:
            historical_bundle = ROOT.parents[1] / "dist" / historical["distribution_path"]
            self.assertEqual(historical["size"], historical_bundle.stat().st_size)
            self.assertEqual(historical["sha256"], hashlib.sha256(historical_bundle.read_bytes()).hexdigest())
        bundle = ROOT.parents[1] / "dist" / package["distribution_path"]
        self.assertEqual(package["size"], bundle.stat().st_size)
        self.assertEqual(package["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        with zipfile.ZipFile(bundle) as archive:
            identity = json.loads(archive.read(
                f"x86qw-installer-{package['version']}/_x86qw/installer.json"
            ))
        self.assertEqual(
            {"format": 1, "project": "x86qw", "version": package["version"]},
            identity,
        )
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

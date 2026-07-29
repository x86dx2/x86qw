from html.parser import HTMLParser
import hashlib
import json
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
        package = next(item for item in catalog["packages"] if item.get("package") == "x86qw-installer")
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
        for name in ("install.sh", "install.ps1"):
            script = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(package["sha256"], script)
            self.assertNotIn("__X86QW_INSTALLER_SHA256__", script)
        home = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertRegex(home, re.escape('https://x86qw.x86.com.br/install.sh'))
        self.assertIn("data-copy-install", home)


if __name__ == "__main__":
    unittest.main()

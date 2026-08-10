from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from x86qw_runtime.installation_changes import (
    InstallationChange,
    ManagedInstallationFile,
    inspect_installation_changes,
    render_installation_gitignore,
)


class InstallationChangesTests(unittest.TestCase):
    def test_reports_added_modified_and_deleted_files_against_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            (target / "qw/default.cfg").write_bytes(b"original\n")
            (target / "qw/changed.cfg").write_bytes(b"changed\n")
            (target / "qw/personal.cfg").write_bytes(b"personal\n")
            (target / ".x86qw").mkdir()
            (target / ".x86qw/state.json").write_text("{}\n", encoding="utf-8")
            (target / "ezQuake Stable.app").mkdir()
            (target / "ezQuake Stable.app/client").write_bytes(b"runtime")
            (target / ".gitignore").write_text("/.gitignore\n", encoding="utf-8")

            managed = {
                "qw/default.cfg": ManagedInstallationFile(
                    component="visual-core",
                    sha256="25718360e05d3c2d0963d1381e9dd4dae5fca789244ee4b9f861adcc0cc96218",
                ),
                "qw/changed.cfg": ManagedInstallationFile(
                    component="ktx",
                    sha256="1ea7a9b77da8c725742658e48d686d50bdaaf7f8b0289b1061adec3d249e5071",
                ),
                "qw/missing.cfg": ManagedInstallationFile(
                    component="ktx",
                    sha256="1ea7a9b77da8c725742658e48d686d50bdaaf7f8b0289b1061adec3d249e5071",
                ),
            }

            self.assertEqual(
                (
                    InstallationChange("M", "qw/changed.cfg", "ktx"),
                    InstallationChange("D", "qw/missing.cfg", "ktx"),
                    InstallationChange("A", "qw/personal.cfg", None),
                ),
                inspect_installation_changes(
                    target,
                    managed,
                    ignored_paths=(
                        ".gitignore",
                        ".x86qw",
                        "ezQuake Stable.app",
                    ),
                ),
            )

    def test_generated_gitignore_hides_exact_baseline_but_exposes_new_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            target = repository / "quake-world"
            (target / "qw").mkdir(parents=True)
            (target / "ezquake/configs").mkdir(parents=True)
            (target / ".x86qw").mkdir()
            (target / "qw/default.cfg").write_text("original\n", encoding="utf-8")
            (target / "ezquake/configs/config.cfg").write_text("personal\n", encoding="utf-8")
            (target / ".x86qw/state.json").write_text("{}\n", encoding="utf-8")
            (target / ".gitignore").write_text(
                render_installation_gitignore(
                    ("qw/default.cfg",),
                    ignored_paths=(".gitignore", ".x86qw"),
                ),
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)

            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                "?? quake-world/ezquake/configs/config.cfg\n",
                status.stdout,
            )

    def test_gitignore_deduplicates_operational_and_managed_paths(self) -> None:
        rendered = render_installation_gitignore(
            ("LICENSE", "qw/default.cfg"),
            ignored_paths=("LICENSE", ".x86qw"),
        )

        self.assertEqual(1, rendered.count("/LICENSE\n"))


if __name__ == "__main__":
    unittest.main()

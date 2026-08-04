import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
install_qw = sys.modules.get("manager")
if install_qw is None:
    SPEC = importlib.util.spec_from_file_location(
        "manager", ROOT / "dist/installer/bin/manager.py",
    )
    install_qw = importlib.util.module_from_spec(SPEC)
    assert SPEC.loader is not None
    sys.modules[SPEC.name] = install_qw
    SPEC.loader.exec_module(install_qw)

play_qw = sys.modules.get("gameplay")
if play_qw is None:
    PLAY_SPEC = importlib.util.spec_from_file_location(
        "gameplay", ROOT / "dist/installer/bin/gameplay.py",
    )
    play_qw = importlib.util.module_from_spec(PLAY_SPEC)
    assert PLAY_SPEC.loader is not None
    sys.modules[PLAY_SPEC.name] = play_qw
    PLAY_SPEC.loader.exec_module(play_qw)

play_qw.configure_context(install_qw.gameplay_composition_context(play_qw))


class GeneratedComponentTransactionTests(unittest.TestCase):
    """Generated payload must remain reversible until state.json commits."""

    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root: Path):
        target = root / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return install_qw.Installer(ROOT, target, cache), target

    def make_player(self, root: Path):
        target = root / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return play_qw.Player(ROOT, target, cache), target

    @staticmethod
    def component_snapshot(target: Path, component: str) -> dict[str, bytes]:
        root = target / ".x86qw/components" / component
        return {
            str(path.relative_to(target)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def seed_td2(self, installer, target: Path, payload: bytes = b"td2-v1") -> Path:
        installer._create_stage(".seed-td2.")
        assert installer.stage is not None
        managed = installer.stage / "managed"
        program = managed / "td2/qwprogs.dat"
        program.parent.mkdir(parents=True)
        program.write_bytes(payload)
        installer.install_component_overlay(
            "total-destruction-2", managed, "test", "x86QW test fixture",
        )
        installer.cleanup_stage()
        installer.stage = None
        return target / "td2/qwprogs.dat"

    @staticmethod
    def fail_state_commit():
        raise install_qw.PersistenceError(
            "falha injetada ao gravar state.json", committed=False,
        )

    def test_package_order_update_rolls_back_when_state_write_fails(self):
        """Dropping the returned result would leave the new pak.lst published."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            qw = target / "qw"
            qw.mkdir()
            (qw / "ktx.pk3").write_bytes(b"ktx")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.refresh_qw_package_order()
            old_payload = (qw / "pak.lst").read_bytes()
            old_metadata = self.component_snapshot(target, "package-order")
            (qw / "nquake.pk3").write_bytes(b"nquake")

            with self.assertRaises(install_qw.PersistenceError):
                with installer.component_state_transaction() as results:
                    created = installer.refresh_qw_package_order(
                        mutation_results=results,
                    )
                    self.assertEqual(1, len(created))
                    self.assertIs(created[0], results[-1])
                    self.fail_state_commit()

            self.assertEqual(old_payload, (qw / "pak.lst").read_bytes())
            self.assertEqual(
                old_metadata, self.component_snapshot(target, "package-order"),
            )
            self.assertIsNone(installer.stage)

    def test_package_order_removal_rolls_back_and_preserves_modified_file(self):
        """Removal must use the reversible component-removal transaction."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            qw = target / "qw"
            qw.mkdir()
            package = qw / "ktx.pk3"
            package.write_bytes(b"ktx")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.refresh_qw_package_order()
            order = qw / "pak.lst"
            order.write_text("personal-order.pk3\n", encoding="utf-8")
            old_metadata = self.component_snapshot(target, "package-order")
            package.unlink()

            with self.assertRaises(install_qw.PersistenceError):
                with installer.component_state_transaction() as results:
                    created = installer.refresh_qw_package_order(
                        mutation_results=results,
                    )
                    self.assertEqual("component-remove:package-order", created[0].plan.identifier)
                    self.fail_state_commit()

            self.assertEqual("personal-order.pk3\n", order.read_text(encoding="utf-8"))
            self.assertEqual(
                old_metadata, self.component_snapshot(target, "package-order"),
            )

    def test_package_order_noop_does_not_create_a_stage_or_result(self):
        """A converged generated component should not be rewritten."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            qw = target / "qw"
            qw.mkdir()
            (qw / "ktx.pk3").write_bytes(b"ktx")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.refresh_qw_package_order()
            before = self.component_snapshot(target, "package-order")
            results = []

            with contextlib.redirect_stdout(io.StringIO()):
                created = installer.refresh_qw_package_order(
                    mutation_results=results,
                )

            self.assertEqual((), created)
            self.assertEqual([], results)
            self.assertIsNone(installer.stage)
            self.assertEqual(before, self.component_snapshot(target, "package-order"))

    def test_play_support_update_rolls_back_but_preserves_personal_config(self):
        """State failure restores derived gamecode without touching user config."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            upstream = self.seed_td2(installer, target)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(installer.reconcile_play_support())
            derived = target / "td2/x86qw_td2.dat"
            personal = target / "td2/x86qw-td2-user.cfg"
            personal.write_text('bind MOUSE4 "impulse 23"\n', encoding="utf-8")
            old_metadata = self.component_snapshot(target, "play-support")
            upstream.write_bytes(b"td2-v2")

            with self.assertRaises(install_qw.PersistenceError):
                with installer.component_state_transaction() as results:
                    self.assertTrue(installer.reconcile_play_support(
                        mutation_results=results,
                    ))
                    self.assertTrue(any(
                        result.plan.identifier == "component:play-support"
                        for result in results
                    ))
                    self.fail_state_commit()

            self.assertEqual(b"td2-v1", derived.read_bytes())
            self.assertEqual(
                'bind MOUSE4 "impulse 23"\n', personal.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                old_metadata, self.component_snapshot(target, "play-support"),
            )

    def test_play_support_removal_returns_result_and_rolls_back_with_state(self):
        """An empty game set must remove play-support through a retained inverse."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            upstream = self.seed_td2(installer, target)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(installer.reconcile_play_support())
            derived = target / "td2/x86qw_td2.dat"
            old_metadata = self.component_snapshot(target, "play-support")
            upstream.unlink()
            component_metadata = target / ".x86qw/components/total-destruction-2"
            install_qw.remove_path(component_metadata)

            with self.assertRaises(install_qw.PersistenceError):
                with installer.component_state_transaction() as results:
                    changed, created = installer.reconcile_play_support_transaction(
                        mutation_results=results,
                    )
                    self.assertTrue(changed)
                    self.assertEqual(1, len(created))
                    self.assertIs(created[0], results[-1])
                    self.assertEqual(
                        "component-remove:play-support",
                        created[0].plan.identifier,
                    )
                    self.assertFalse(derived.exists())
                    self.fail_state_commit()

            self.assertEqual(b"td2-v1", derived.read_bytes())
            self.assertEqual(
                old_metadata, self.component_snapshot(target, "play-support"),
            )

    def test_play_support_dry_run_and_noop_do_not_mutate(self):
        """Planning and a converged repair must not publish generated payload."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            self.seed_td2(installer, target)
            rows = []
            results = []

            self.assertTrue(installer.reconcile_play_support(
                dry_run=True, plan_rows=rows, mutation_results=results,
            ))
            self.assertTrue(rows)
            self.assertEqual([], results)
            self.assertFalse((target / "td2/x86qw_td2.dat").exists())
            self.assertIsNone(installer.stage)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(installer.reconcile_play_support())
            results = []
            self.assertFalse(installer.reconcile_play_support(
                mutation_results=results,
            ))
            self.assertEqual([], results)
            self.assertIsNone(installer.stage)

    def test_generated_stage_survives_an_incomplete_parent_rollback(self):
        """Recovery backups must remain inspectable if the inverse itself fails."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            qw = target / "qw"
            qw.mkdir()
            (qw / "ktx.pk3").write_bytes(b"ktx")
            held_stage = None

            with mock.patch.object(
                installer,
                "rollback_component_transactions",
                side_effect=install_qw.InstallerError("rollback injetado incompleto"),
            ):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "rollback injetado incompleto",
                ):
                    with installer.component_state_transaction() as results:
                        installer.refresh_qw_package_order(
                            mutation_results=results,
                        )
                        held_stage = installer.stage
                        self.fail_state_commit()

            self.assertIsNotNone(held_stage)
            assert held_stage is not None
            self.assertEqual(held_stage, installer.stage)
            self.assertTrue(held_stage.is_dir())
            installer.cleanup_stage()
            installer.stage = None

    def test_isolated_package_order_preserves_stage_on_incomplete_inverse(self):
        """The compatibility wrapper must not erase recovery evidence."""

        error = install_qw.MutationRollbackError(
            "rollback incompleto injetado",
            plan_identifier="component:package-order",
            step_key="payload",
            operation_error=RuntimeError("apply failed"),
            rollback_errors=(("payload", RuntimeError("inverse failed")),),
        )
        with tempfile.TemporaryDirectory() as temporary:
            installer, target = self.make_installer(Path(temporary))
            qw = target / "qw"
            qw.mkdir()
            (qw / "ktx.pk3").write_bytes(b"ktx")

            with mock.patch.object(
                installer, "install_component_overlay_transaction", side_effect=error,
            ), self.assertRaises(install_qw.MutationRollbackError):
                installer.refresh_qw_package_order()

            self.assertIsNotNone(installer.stage)
            assert installer.stage is not None
            self.assertTrue(installer.stage.is_dir())
            installer.cleanup_stage()
            installer.stage = None

    def test_isolated_play_support_preserves_stage_on_incomplete_inverse(self):
        """Standalone gameplay repair must retain its failed inverse workspace."""

        error = install_qw.MutationRollbackError(
            "rollback incompleto injetado",
            plan_identifier="component:play-support",
            step_key="payload",
            operation_error=RuntimeError("apply failed"),
            rollback_errors=(("payload", RuntimeError("inverse failed")),),
        )
        with tempfile.TemporaryDirectory() as temporary:
            player, target = self.make_player(Path(temporary))
            self.seed_td2(player, target)
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")

            with mock.patch.object(
                player, "install_component_overlay_transaction", side_effect=error,
            ), self.assertRaises(install_qw.MutationRollbackError):
                player.ensure_local_play_support([game])

            self.assertIsNotNone(player.stage)
            assert player.stage is not None
            self.assertTrue(player.stage.is_dir())
            player.cleanup_stage()
            player.stage = None


if __name__ == "__main__":
    unittest.main()

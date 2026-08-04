from __future__ import annotations

import importlib
import unittest


class RuntimeTransactionTests(unittest.TestCase):
    def test_prepared_plan_applies_the_exact_confirmed_plan(self) -> None:
        """Replanning after confirmation could apply a different step sequence."""

        transaction = importlib.import_module("x86qw_runtime.transaction")
        events: list[str] = []
        plan = transaction.MutationPlan(
            identifier="component:ktx",
            summary="instalar KTX",
            steps=(
                transaction.MutationStep(
                    key="payload",
                    description="publicar payload",
                    observe=lambda: "payload-v1",
                    apply=lambda: events.append("apply:payload") or "payload-token",
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
                transaction.MutationStep(
                    key="metadata",
                    description="publicar metadados",
                    observe=lambda: "metadata-v1",
                    apply=lambda: events.append("apply:metadata") or "metadata-token",
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
            ),
        )

        prepared = transaction.prepare_mutation(plan)
        result = transaction.execute_mutation(prepared)

        self.assertIs(prepared.plan, plan)
        self.assertIs(result.plan, plan)
        self.assertEqual(result.applied_steps, ("payload", "metadata"))
        self.assertEqual(events, ["apply:payload", "apply:metadata"])

    def test_precondition_drift_aborts_before_the_first_mutation(self) -> None:
        """A changed target after confirmation must cause zero writes."""

        transaction = importlib.import_module("x86qw_runtime.transaction")
        state = {"payload": "v1", "metadata": "v1"}
        applied: list[str] = []
        plan = transaction.MutationPlan(
            identifier="component:ktx",
            summary="instalar KTX",
            steps=(
                transaction.MutationStep(
                    key="payload",
                    description="publicar payload",
                    observe=lambda: state["payload"],
                    apply=lambda: applied.append("payload"),
                    rollback=lambda token: None,
                ),
                transaction.MutationStep(
                    key="metadata",
                    description="publicar metadados",
                    observe=lambda: state["metadata"],
                    apply=lambda: applied.append("metadata"),
                    rollback=lambda token: None,
                ),
            ),
        )
        prepared = transaction.prepare_mutation(plan)
        state["metadata"] = "v2"

        with self.assertRaises(transaction.MutationPreconditionError) as raised:
            transaction.execute_mutation(prepared)

        self.assertEqual(raised.exception.step_key, "metadata")
        self.assertEqual(applied, [])

    def test_failed_step_rolls_back_completed_steps_in_reverse_order(self) -> None:
        """A partial transaction must not leave earlier mutations installed."""

        transaction = importlib.import_module("x86qw_runtime.transaction")
        events: list[str] = []

        def fail() -> None:
            events.append("apply:receipt")
            raise OSError("receipt unavailable")

        plan = transaction.MutationPlan(
            identifier="component:ktx",
            summary="instalar KTX",
            steps=(
                transaction.MutationStep(
                    key="payload",
                    description="payload",
                    observe=lambda: None,
                    apply=lambda: events.append("apply:payload") or "payload",
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
                transaction.MutationStep(
                    key="inventory",
                    description="inventory",
                    observe=lambda: None,
                    apply=lambda: events.append("apply:inventory") or "inventory",
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
                transaction.MutationStep(
                    key="receipt",
                    description="receipt",
                    observe=lambda: None,
                    apply=fail,
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
            ),
        )

        with self.assertRaises(transaction.MutationApplyError) as raised:
            transaction.execute_mutation(transaction.prepare_mutation(plan))

        self.assertEqual(raised.exception.step_key, "receipt")
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertEqual(
            events,
            [
                "apply:payload",
                "apply:inventory",
                "apply:receipt",
                "rollback:inventory",
                "rollback:payload",
            ],
        )

    def test_rollback_failure_reports_original_and_residual_errors(self) -> None:
        """A rollback error must not hide the operation that triggered rollback."""

        transaction = importlib.import_module("x86qw_runtime.transaction")

        def fail_apply() -> None:
            raise OSError("apply failed")

        def fail_rollback(token: object) -> None:
            raise PermissionError(f"rollback failed for {token}")

        plan = transaction.MutationPlan(
            identifier="component:ktx",
            summary="instalar KTX",
            steps=(
                transaction.MutationStep(
                    key="payload",
                    description="payload",
                    observe=lambda: None,
                    apply=lambda: "payload-token",
                    rollback=fail_rollback,
                ),
                transaction.MutationStep(
                    key="metadata",
                    description="metadata",
                    observe=lambda: None,
                    apply=fail_apply,
                    rollback=lambda token: None,
                ),
            ),
        )

        with self.assertRaises(transaction.MutationRollbackError) as raised:
            transaction.execute_mutation(transaction.prepare_mutation(plan))

        self.assertEqual(raised.exception.step_key, "metadata")
        self.assertIsInstance(raised.exception.operation_error, OSError)
        self.assertEqual(len(raised.exception.rollback_errors), 1)
        self.assertEqual(raised.exception.rollback_errors[0][0], "payload")

    def test_successful_subtransaction_can_be_rolled_back_by_its_parent(self) -> None:
        """A later state failure must be able to reverse an installed component."""

        transaction = importlib.import_module("x86qw_runtime.transaction")
        events: list[str] = []
        plan = transaction.MutationPlan(
            identifier="component:ktx",
            summary="instalar KTX",
            steps=(
                transaction.MutationStep(
                    key="payload",
                    description="payload",
                    observe=lambda: None,
                    apply=lambda: events.append("apply:payload") or "payload",
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
                transaction.MutationStep(
                    key="metadata",
                    description="metadata",
                    observe=lambda: None,
                    apply=lambda: events.append("apply:metadata") or "metadata",
                    rollback=lambda token: events.append(f"rollback:{token}"),
                ),
            ),
        )
        result = transaction.execute_mutation(transaction.prepare_mutation(plan))

        transaction.rollback_mutation(result)

        self.assertEqual(
            events,
            [
                "apply:payload",
                "apply:metadata",
                "rollback:metadata",
                "rollback:payload",
            ],
        )


if __name__ == "__main__":
    unittest.main()

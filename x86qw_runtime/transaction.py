"""Immutable mutation plans with preflight, ordered apply, and reverse rollback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import InstallerError, PersistenceError


Observe = Callable[[], object]
Apply = Callable[[], object]
Rollback = Callable[[object], None]
Finalize = Callable[[object], None]


def _retain_rollback_material(_token: object) -> None:
    """Default finalizer for mutations whose rollback token needs no disposal."""


class MutationError(InstallerError):
    """A prepared installation mutation could not complete safely."""

    def __init__(self, message: str, *, plan_identifier: str, step_key: str) -> None:
        super().__init__(message)
        self.plan_identifier = plan_identifier
        self.step_key = step_key


class MutationPreconditionError(MutationError):
    """The installation changed between planning and application."""


class MutationApplyError(MutationError):
    """A mutation step failed and every completed step was rolled back."""

    def __init__(
        self,
        message: str,
        *,
        plan_identifier: str,
        step_key: str,
        operation_error: BaseException,
    ) -> None:
        super().__init__(
            message, plan_identifier=plan_identifier, step_key=step_key,
        )
        self.operation_error = operation_error


class MutationRollbackError(MutationApplyError):
    """Application failed and at least one inverse could not be completed."""

    def __init__(
        self,
        message: str,
        *,
        plan_identifier: str,
        step_key: str,
        operation_error: BaseException,
        rollback_errors: tuple[tuple[str, BaseException], ...],
    ) -> None:
        super().__init__(
            message,
            plan_identifier=plan_identifier,
            step_key=step_key,
            operation_error=operation_error,
        )
        self.rollback_errors = rollback_errors


class MutationCommittedError(MutationApplyError):
    """A persistence barrier committed, but its durability could not be proven."""

    def __init__(
        self,
        message: str,
        *,
        plan_identifier: str,
        step_key: str,
        operation_error: BaseException,
        committed_steps: tuple[str, ...],
    ) -> None:
        super().__init__(
            message,
            plan_identifier=plan_identifier,
            step_key=step_key,
            operation_error=operation_error,
        )
        self.committed_steps = committed_steps


@dataclass(frozen=True)
class MutationStep:
    """One planned change and its exact inverse."""

    key: str
    description: str
    observe: Observe
    apply: Apply
    rollback: Rollback
    finalize: Finalize = _retain_rollback_material

    def __post_init__(self) -> None:
        if not self.key or not self.description:
            raise ValueError("mutation step key and description must be non-empty")
        for value in (self.observe, self.apply, self.rollback, self.finalize):
            if not callable(value):
                raise TypeError("mutation step operations must be callable")


@dataclass(frozen=True)
class MutationPlan:
    """The exact ordered set of changes presented for confirmation."""

    identifier: str
    summary: str
    steps: tuple[MutationStep, ...]

    def __post_init__(self) -> None:
        if not self.identifier or not self.summary:
            raise ValueError("mutation plan identity and summary must be non-empty")
        if not isinstance(self.steps, tuple) or not self.steps:
            raise ValueError("mutation plan must contain at least one step")
        keys = tuple(step.key for step in self.steps)
        if len(keys) != len(set(keys)):
            raise ValueError("mutation plan step keys must be unique")


@dataclass(frozen=True)
class _PreparedStep:
    step: MutationStep
    observation: object


@dataclass(frozen=True)
class PreparedMutation:
    """A plan bound to the installation snapshot inspected before confirmation."""

    plan: MutationPlan
    steps: tuple[_PreparedStep, ...]


@dataclass(frozen=True)
class MutationResult:
    """A successfully applied plan."""

    plan: MutationPlan
    applied_steps: tuple[str, ...]
    _completed: tuple[tuple[MutationStep, object], ...] = field(
        repr=False, compare=False,
    )


def prepare_mutation(plan: MutationPlan) -> PreparedMutation:
    """Capture every precondition without changing the installation."""

    if not isinstance(plan, MutationPlan):
        raise TypeError("plan must be MutationPlan")
    prepared: list[_PreparedStep] = []
    for step in plan.steps:
        try:
            observation = step.observe()
        except BaseException as error:
            raise MutationPreconditionError(
                f"Não foi possível inspecionar a etapa {step.key} do plano {plan.identifier}.",
                plan_identifier=plan.identifier,
                step_key=step.key,
            ) from error
        prepared.append(_PreparedStep(step, observation))
    return PreparedMutation(plan, tuple(prepared))


def _revalidate(prepared: PreparedMutation) -> None:
    for item in prepared.steps:
        try:
            current = item.step.observe()
        except BaseException as error:
            raise MutationPreconditionError(
                f"A etapa {item.step.key} não pôde ser revalidada antes da mutação.",
                plan_identifier=prepared.plan.identifier,
                step_key=item.step.key,
            ) from error
        if current != item.observation:
            raise MutationPreconditionError(
                f"A instalação mudou após a confirmação na etapa {item.step.key}.",
                plan_identifier=prepared.plan.identifier,
                step_key=item.step.key,
            )


def execute_mutation(prepared: PreparedMutation) -> MutationResult:
    """Revalidate once, apply in order, and roll completed steps back in reverse."""

    if not isinstance(prepared, PreparedMutation):
        raise TypeError("prepared must be PreparedMutation")
    _revalidate(prepared)
    completed: list[tuple[MutationStep, object]] = []
    for item in prepared.steps:
        try:
            rollback_token = item.step.apply()
        except BaseException as operation_error:
            if (
                isinstance(operation_error, PersistenceError)
                and operation_error.committed
            ):
                raise MutationCommittedError(
                    f"A etapa {item.step.key} foi promovida, mas sua durabilidade não pôde ser confirmada; efeitos preservados.",
                    plan_identifier=prepared.plan.identifier,
                    step_key=item.step.key,
                    operation_error=operation_error,
                    committed_steps=tuple(
                        [*(step.key for step, _ in completed), item.step.key]
                    ),
                ) from operation_error
            rollback_errors: list[tuple[str, BaseException]] = []
            for completed_step, token in reversed(completed):
                try:
                    completed_step.rollback(token)
                except BaseException as rollback_error:
                    rollback_errors.append((completed_step.key, rollback_error))
            if rollback_errors:
                raise MutationRollbackError(
                    f"A etapa {item.step.key} falhou e o rollback ficou incompleto.",
                    plan_identifier=prepared.plan.identifier,
                    step_key=item.step.key,
                    operation_error=operation_error,
                    rollback_errors=tuple(rollback_errors),
                ) from operation_error
            raise MutationApplyError(
                f"A etapa {item.step.key} falhou; alterações anteriores foram revertidas.",
                plan_identifier=prepared.plan.identifier,
                step_key=item.step.key,
                operation_error=operation_error,
            ) from operation_error
        completed.append((item.step, rollback_token))
    return MutationResult(
        plan=prepared.plan,
        applied_steps=tuple(step.key for step, _ in completed),
        _completed=tuple(completed),
    )


def rollback_mutation(result: MutationResult) -> None:
    """Reverse a completed subtransaction when a parent transaction fails later."""

    if not isinstance(result, MutationResult):
        raise TypeError("result must be MutationResult")
    rollback_errors: list[tuple[str, BaseException]] = []
    for step, token in reversed(result._completed):
        try:
            step.rollback(token)
        except BaseException as error:
            rollback_errors.append((step.key, error))
    if rollback_errors:
        operation_error = RuntimeError("parent transaction requested rollback")
        raise MutationRollbackError(
            "O rollback da transação concluída ficou incompleto.",
            plan_identifier=result.plan.identifier,
            step_key=rollback_errors[0][0],
            operation_error=operation_error,
            rollback_errors=tuple(rollback_errors),
        ) from operation_error


def finalize_mutation(result: MutationResult) -> None:
    """Discard rollback material after the parent transaction commits logically."""

    if not isinstance(result, MutationResult):
        raise TypeError("result must be MutationResult")
    failures: list[tuple[str, BaseException]] = []
    for step, token in result._completed:
        try:
            step.finalize(token)
        except BaseException as error:
            failures.append((step.key, error))
    if failures:
        step_key, operation_error = failures[0]
        detail = "; ".join(f"{key}: {error}" for key, error in failures)
        raise MutationCommittedError(
            f"A transação foi confirmada, mas a finalização ficou incompleta: {detail}",
            plan_identifier=result.plan.identifier,
            step_key=step_key,
            operation_error=operation_error,
            committed_steps=result.applied_steps,
        ) from operation_error

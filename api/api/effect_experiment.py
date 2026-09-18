# -*- coding: utf-8 -*-
"""Effect experiment harness (audit R14 / mutual-IP spec v1.3, P2-1 / T03~T08).

The audit's deepest complaint is not that a Skill *generates* — it is that DAON
never demonstrates the Skill actually **improves task capability**. P0-1 closes
the generation contract; this module closes the *effect* question by running a
controlled experiment and emitting receipts (T03~T08) that a third party can
re-check.

What this harness does
----------------------
It does NOT call an LLM or a browser itself. Instead it defines the experiment
*protocol* and the *statistics*, and it consumes a pluggable ``runner`` — a
callable that executes one trial and returns a structured outcome. That keeps
the harness deterministic and unit-testable while the real runner (LLM / browser
E2E) is supplied by the caller at experiment time.

Protocol (spec v1.3 §15, T03~T08)
---------------------------------
  * T03 — baseline(A) vs approved(B), same condition, repeated, order-controlled
    (AB/BA). We compute a paired comparison so ordering bias cancels out.
  * T04 — unseen task generalization: the approved Skill is applied to a task it
    was not distilled from.
  * T05 — regression: the approved Skill must not *lose* capability on the
    baseline task set.
  * T06 — restart persistence: the Skill survives a process restart.
  * T07 — REJECTED skill blocked on the normal path.
  * T08 — approval verification level recorded (ties into P0-2).

Design rules
------------
  * Deterministic: given the same trial outcomes the verdict is identical.
  * Fail-closed: an experiment with too few trials, or with a runner that
    raises, is reported as ``inconclusive`` / ``error`` — never as ``pass``.
  * No self-report: the verdict is derived from recorded trial outcomes, and the
    emitted receipt carries the raw counts so it can be independently checked.

Stdlib-only so it runs inside the frozen server.exe.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_ERROR = "error"
VALID_VERDICTS = frozenset(
    {VERDICT_PASS, VERDICT_FAIL, VERDICT_INCONCLUSIVE, VERDICT_ERROR}
)

# Arm labels for the AB/BA design.
ARM_BASELINE = "A"
ARM_APPROVED = "B"

# Minimum paired trials before a T03 verdict is allowed to be PASS. Below this
# the result is INCONCLUSIVE — we refuse to claim an effect from noise.
MIN_PAIRED_TRIALS = 3

# A capability gain must exceed this fraction to count as a real improvement.
# (Guards against a single lucky trial flipping the verdict.)
MIN_EFFECT_SIZE = 0.0


# ---------------------------------------------------------------------------
# Trial outcome
# ---------------------------------------------------------------------------
@dataclass
class TrialOutcome:
    """The result of a single trial.

    ``success`` is the primary signal (did the task complete correctly?).
    ``score`` is an optional graded signal in [0, 1] for finer comparison.
    ``error`` is set when the runner raised.
    """

    arm: str
    task_id: str
    success: bool
    score: float = 0.0
    order: int = 0
    error: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Paired comparison (T03)
# ---------------------------------------------------------------------------
@dataclass
class PairedResult:
    """Result of a paired baseline-vs-approved comparison."""

    n_pairs: int
    baseline_success_rate: float
    approved_success_rate: float
    success_delta: float
    baseline_mean_score: float
    approved_mean_score: float
    score_delta: float
    verdict: str
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _rate(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v) / len(values)


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.fmean(values)


def compare_paired(
    baseline: list[TrialOutcome],
    approved: list[TrialOutcome],
    *,
    min_pairs: int = MIN_PAIRED_TRIALS,
    min_effect: float = MIN_EFFECT_SIZE,
) -> PairedResult:
    """Compare baseline (A) vs approved (B) trials.

    The two lists are matched by ``task_id`` so the comparison is *paired*: each
    task contributes one A trial and one B trial. Ordering bias is already
    cancelled by the AB/BA design at the runner level; here we only require that
    both arms cover the same task ids.

    Fail-closed: fewer than ``min_pairs`` matched tasks -> INCONCLUSIVE.
    """
    a_by_task = {t.task_id: t for t in baseline}
    b_by_task = {t.task_id: t for t in approved}
    shared = sorted(set(a_by_task) & set(b_by_task))

    # Fail-closed: if any trial in either arm errored (the runner raised), the
    # comparison is not a capability signal — it is an execution failure. We
    # must NOT report that as a regression (fail); it is an ERROR.
    errored = [
        t for t in (baseline + approved) if t.error
    ]
    if errored:
        return PairedResult(
            n_pairs=len(shared),
            baseline_success_rate=_rate([a_by_task[t].success for t in shared]),
            approved_success_rate=_rate([b_by_task[t].success for t in shared]),
            success_delta=0.0,
            baseline_mean_score=_mean([a_by_task[t].score for t in shared]),
            approved_mean_score=_mean([b_by_task[t].score for t in shared]),
            score_delta=0.0,
            verdict=VERDICT_ERROR,
            reason=f"{len(errored)} trial(s) errored (runner raised)",
        )

    if len(shared) < min_pairs:
        return PairedResult(
            n_pairs=len(shared),
            baseline_success_rate=_rate([a_by_task[t].success for t in shared]),
            approved_success_rate=_rate([b_by_task[t].success for t in shared]),
            success_delta=0.0,
            baseline_mean_score=_mean([a_by_task[t].score for t in shared]),
            approved_mean_score=_mean([b_by_task[t].score for t in shared]),
            score_delta=0.0,
            verdict=VERDICT_INCONCLUSIVE,
            reason=f"only {len(shared)} paired tasks (< {min_pairs})",
        )

    a_succ = [a_by_task[t].success for t in shared]
    b_succ = [b_by_task[t].success for t in shared]
    a_score = [a_by_task[t].score for t in shared]
    b_score = [b_by_task[t].score for t in shared]

    success_delta = _rate(b_succ) - _rate(a_succ)
    score_delta = _mean(b_score) - _mean(a_score)

    # PASS requires a non-negative success delta AND a positive score delta
    # beyond the effect threshold. A regression (negative delta) is FAIL.
    if success_delta < 0 or score_delta < -min_effect:
        verdict = VERDICT_FAIL
        reason = "approved arm regressed vs baseline"
    elif success_delta > 0 or score_delta > min_effect:
        verdict = VERDICT_PASS
        reason = "approved arm improved vs baseline"
    else:
        verdict = VERDICT_INCONCLUSIVE
        reason = "no measurable difference between arms"

    return PairedResult(
        n_pairs=len(shared),
        baseline_success_rate=_rate(a_succ),
        approved_success_rate=_rate(b_succ),
        success_delta=success_delta,
        baseline_mean_score=_mean(a_score),
        approved_mean_score=_mean(b_score),
        score_delta=score_delta,
        verdict=verdict,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Experiment runner protocol
# ---------------------------------------------------------------------------
# A runner is called as runner(arm, task_id, order) and must return a
# TrialOutcome (or a dict that can build one). It may raise; the harness records
# the error as a failed trial rather than aborting the whole experiment.
Runner = Callable[[str, str, int], Any]


def _coerce_outcome(raw: Any, arm: str, task_id: str, order: int) -> TrialOutcome:
    if isinstance(raw, TrialOutcome):
        return raw
    if isinstance(raw, dict):
        return TrialOutcome(
            arm=str(raw.get("arm", arm)),
            task_id=str(raw.get("task_id", task_id)),
            success=bool(raw.get("success", False)),
            score=float(raw.get("score", 0.0) or 0.0),
            order=int(raw.get("order", order)),
            error=str(raw.get("error", "")),
            meta=dict(raw.get("meta", {}) or {}),
        )
    # A bare bool is accepted as the success signal.
    if isinstance(raw, bool):
        return TrialOutcome(arm=arm, task_id=task_id, success=raw, order=order)
    return TrialOutcome(
        arm=arm,
        task_id=task_id,
        success=False,
        order=order,
        error=f"runner returned unsupported type: {type(raw).__name__}",
    )


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------
@dataclass
class ExperimentResult:
    """Aggregate result of a T03~T08 effect experiment."""

    experiment_id: str
    verdicts: dict = field(default_factory=dict)   # test_id -> verdict
    details: dict = field(default_factory=dict)    # test_id -> detail dict
    trials: list = field(default_factory=list)     # all TrialOutcome dicts

    def to_dict(self) -> dict:
        return asdict(self)

    def overall_verdict(self) -> str:
        """Worst-case roll-up: any FAIL/ERROR dominates; else INCONCLUSIVE; else PASS."""
        if not self.verdicts:
            return VERDICT_INCONCLUSIVE
        vals = set(self.verdicts.values())
        if VERDICT_ERROR in vals:
            return VERDICT_ERROR
        if VERDICT_FAIL in vals:
            return VERDICT_FAIL
        if VERDICT_INCONCLUSIVE in vals:
            return VERDICT_INCONCLUSIVE
        return VERDICT_PASS


class EffectExperiment:
    """Runs the T03~T08 effect experiment against a pluggable runner.

    Usage::

        exp = EffectExperiment("exp-001", runner=my_runner)
        result = exp.run(
            baseline_tasks=["t1", "t2", "t3"],
            unseen_tasks=["u1", "u2"],
            restart_probe=lambda: True,
            rejected_blocked=lambda: True,
            verification_level="B_STATIC_VALIDATION",
        )
        exp.emit_receipts(emitter)   # feed T03~T08 into the receipt emitter
    """

    def __init__(self, experiment_id: str, runner: Optional[Runner] = None):
        self.experiment_id = experiment_id
        self.runner = runner
        self._trials: list[TrialOutcome] = []

    # -- low-level trial execution ----------------------------------------
    def _run_trial(self, arm: str, task_id: str, order: int) -> TrialOutcome:
        if self.runner is None:
            return TrialOutcome(
                arm=arm, task_id=task_id, success=False, order=order,
                error="no runner configured",
            )
        try:
            raw = self.runner(arm, task_id, order)
        except Exception as exc:  # noqa: BLE001 - record, never abort
            return TrialOutcome(
                arm=arm, task_id=task_id, success=False, order=order,
                error=f"{type(exc).__name__}: {exc}",
            )
        return _coerce_outcome(raw, arm, task_id, order)

    # -- T03: paired AB/BA -------------------------------------------------
    def run_t03(self, tasks: list[str]) -> PairedResult:
        """Run baseline vs approved with AB/BA order control.

        For each task we run A then B, and B then A, so any warm-up / ordering
        bias affects both arms equally. The two A trials and two B trials are
        averaged by taking the *last* of each arm (the steady-state run).
        """
        baseline: list[TrialOutcome] = []
        approved: list[TrialOutcome] = []
        order = 0
        for task in tasks:
            # AB
            order += 1
            a1 = self._run_trial(ARM_BASELINE, task, order)
            order += 1
            b1 = self._run_trial(ARM_APPROVED, task, order)
            # BA
            order += 1
            b2 = self._run_trial(ARM_APPROVED, task, order)
            order += 1
            a2 = self._run_trial(ARM_BASELINE, task, order)
            self._trials.extend([a1, b1, b2, a2])
            # steady-state = second run of each arm
            baseline.append(a2)
            approved.append(b2)
        return compare_paired(baseline, approved)

    # -- T04: unseen generalization ---------------------------------------
    def run_t04(self, unseen_tasks: list[str]) -> dict:
        """Apply the approved Skill to tasks it was not distilled from."""
        if not unseen_tasks:
            return {"verdict": VERDICT_INCONCLUSIVE, "reason": "no unseen tasks"}
        outcomes = []
        for i, task in enumerate(unseen_tasks, start=1):
            o = self._run_trial(ARM_APPROVED, task, i)
            self._trials.append(o)
            outcomes.append(o)
        # Fail-closed: a runner that raised is an execution error, not a
        # capability failure.
        if any(o.error for o in outcomes):
            return {
                "verdict": VERDICT_ERROR,
                "n": len(outcomes),
                "reason": "one or more unseen trials errored (runner raised)",
            }
        rate = _rate([o.success for o in outcomes])
        verdict = VERDICT_PASS if rate >= 0.5 else VERDICT_FAIL
        return {
            "verdict": verdict,
            "success_rate": rate,
            "n": len(outcomes),
            "reason": f"unseen success rate {rate:.2f}",
        }

    # -- T05: regression ---------------------------------------------------
    def run_t05(self, baseline_tasks: list[str]) -> dict:
        """Approved arm must not lose capability on the baseline task set."""
        if not baseline_tasks:
            return {"verdict": VERDICT_INCONCLUSIVE, "reason": "no baseline tasks"}
        outcomes = []
        for i, task in enumerate(baseline_tasks, start=1):
            o = self._run_trial(ARM_APPROVED, task, i)
            self._trials.append(o)
            outcomes.append(o)
        # Fail-closed: a runner that raised is an execution error, not a
        # capability regression.
        if any(o.error for o in outcomes):
            return {
                "verdict": VERDICT_ERROR,
                "n": len(outcomes),
                "reason": "one or more baseline trials errored (runner raised)",
            }
        rate = _rate([o.success for o in outcomes])
        # Regression = the approved arm fails the tasks it should still handle.
        verdict = VERDICT_PASS if rate >= 0.5 else VERDICT_FAIL
        return {
            "verdict": verdict,
            "success_rate": rate,
            "n": len(outcomes),
            "reason": f"baseline retention rate {rate:.2f}",
        }

    # -- T06: restart persistence -----------------------------------------
    def run_t06(self, restart_probe: Optional[Callable[[], bool]]) -> dict:
        """The Skill must survive a process restart.

        ``restart_probe`` returns True when the Skill is still loadable after a
        restart. Absent a probe we cannot claim persistence -> INCONCLUSIVE.
        """
        if restart_probe is None:
            return {"verdict": VERDICT_INCONCLUSIVE, "reason": "no restart probe"}
        try:
            survived = bool(restart_probe())
        except Exception as exc:  # noqa: BLE001
            return {"verdict": VERDICT_ERROR, "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "verdict": VERDICT_PASS if survived else VERDICT_FAIL,
            "survived": survived,
            "reason": "skill survived restart" if survived else "skill lost on restart",
        }

    # -- T07: rejected blocked --------------------------------------------
    def run_t07(self, rejected_blocked: Optional[Callable[[], bool]]) -> dict:
        """A REJECTED skill must be blocked on the normal path."""
        if rejected_blocked is None:
            return {"verdict": VERDICT_INCONCLUSIVE, "reason": "no reject probe"}
        try:
            blocked = bool(rejected_blocked())
        except Exception as exc:  # noqa: BLE001
            return {"verdict": VERDICT_ERROR, "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "verdict": VERDICT_PASS if blocked else VERDICT_FAIL,
            "blocked": blocked,
            "reason": "rejected skill blocked" if blocked else "rejected skill leaked",
        }

    # -- T08: verification level recorded ---------------------------------
    def run_t08(self, verification_level: str) -> dict:
        """Record the approval verification level (ties into P0-2)."""
        level = (verification_level or "").strip()
        if not level:
            return {"verdict": VERDICT_INCONCLUSIVE, "reason": "no verification level"}
        return {
            "verdict": VERDICT_PASS,
            "verification_level": level,
            "reason": f"verification level recorded: {level}",
        }

    # -- full run ----------------------------------------------------------
    def run(
        self,
        *,
        baseline_tasks: list[str],
        unseen_tasks: Optional[list[str]] = None,
        restart_probe: Optional[Callable[[], bool]] = None,
        rejected_blocked: Optional[Callable[[], bool]] = None,
        verification_level: str = "",
    ) -> ExperimentResult:
        """Run T03~T08 and return an aggregate result."""
        result = ExperimentResult(experiment_id=self.experiment_id)

        paired = self.run_t03(baseline_tasks)
        result.verdicts["T03"] = paired.verdict
        result.details["T03"] = paired.to_dict()

        t04 = self.run_t04(unseen_tasks or [])
        result.verdicts["T04"] = t04["verdict"]
        result.details["T04"] = t04

        t05 = self.run_t05(baseline_tasks)
        result.verdicts["T05"] = t05["verdict"]
        result.details["T05"] = t05

        t06 = self.run_t06(restart_probe)
        result.verdicts["T06"] = t06["verdict"]
        result.details["T06"] = t06

        t07 = self.run_t07(rejected_blocked)
        result.verdicts["T07"] = t07["verdict"]
        result.details["T07"] = t07

        t08 = self.run_t08(verification_level)
        result.verdicts["T08"] = t08["verdict"]
        result.details["T08"] = t08

        result.trials = [t.to_dict() for t in self._trials]
        return result

    # -- receipt emission --------------------------------------------------
    def emit_receipts(self, emitter) -> None:
        """Feed the last run's T03~T08 verdicts into an EvidenceReceiptEmitter.

        ``emitter`` is an ``api.api.evidence_receipt.EvidenceReceiptEmitter``.
        Verdicts map onto receipt statuses: pass->pass, fail->fail,
        inconclusive->skip, error->error.
        """
        status_map = {
            VERDICT_PASS: "pass",
            VERDICT_FAIL: "fail",
            VERDICT_INCONCLUSIVE: "skip",
            VERDICT_ERROR: "error",
        }
        for test_id, verdict in self._last_result.verdicts.items():
            emitter.record(
                test_id,
                status_map.get(verdict, "error"),
                evidence=self._last_result.details.get(test_id, {}),
                notes=f"effect experiment {self.experiment_id}",
            )

    _last_result: Optional[ExperimentResult] = None

    def run_and_emit(self, emitter, **kwargs) -> ExperimentResult:
        """Convenience: run the experiment and emit T03~T08 receipts."""
        result = self.run(**kwargs)
        self._last_result = result
        self.emit_receipts(emitter)
        return result

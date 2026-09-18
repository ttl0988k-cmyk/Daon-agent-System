# -*- coding: utf-8 -*-
"""Regression tests for the T03~T08 effect experiment harness (audit R14 / P2-1).

These lock in the experiment protocol:
  * T03 is a *paired* baseline-vs-approved comparison with AB/BA order control,
  * a verdict is fail-closed: too few trials -> inconclusive, never pass,
  * a runner that raises is recorded as an error trial, not a crash,
  * T04~T08 each map to a verdict and feed the receipt emitter,
  * the overall roll-up is worst-case (any fail/error dominates).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Repo root is on sys.path so `api.api.*` is importable.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.api.effect_experiment import (  # noqa: E402
    ARM_APPROVED,
    ARM_BASELINE,
    MIN_PAIRED_TRIALS,
    VALID_VERDICTS,
    VERDICT_ERROR,
    VERDICT_FAIL,
    VERDICT_INCONCLUSIVE,
    VERDICT_PASS,
    EffectExperiment,
    ExperimentResult,
    TrialOutcome,
    compare_paired,
)
from api.api.evidence_receipt import (  # noqa: E402
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIP,
    EvidenceReceiptEmitter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _outcome(arm, task, success, score=0.0, order=0):
    return TrialOutcome(arm=arm, task_id=task, success=success, score=score, order=order)


def _improving_runner(arm, task_id, order):
    """Approved arm always succeeds; baseline always fails."""
    if arm == ARM_APPROVED:
        return {"success": True, "score": 1.0}
    return {"success": False, "score": 0.0}


def _regressing_runner(arm, task_id, order):
    """Approved arm is worse than baseline (a regression)."""
    if arm == ARM_APPROVED:
        return {"success": False, "score": 0.0}
    return {"success": True, "score": 1.0}


def _flat_runner(arm, task_id, order):
    """Both arms identical -> no measurable effect."""
    return {"success": True, "score": 0.5}


def _raising_runner(arm, task_id, order):
    raise RuntimeError("runner exploded")


# ---------------------------------------------------------------------------
# Paired comparison (T03 core)
# ---------------------------------------------------------------------------
class TestComparePaired:
    def test_improvement_passes(self):
        base = [_outcome(ARM_BASELINE, f"t{i}", False) for i in range(4)]
        appr = [_outcome(ARM_APPROVED, f"t{i}", True, score=1.0) for i in range(4)]
        r = compare_paired(base, appr)
        assert r.verdict == VERDICT_PASS
        assert r.success_delta > 0
        assert r.n_pairs == 4

    def test_regression_fails(self):
        base = [_outcome(ARM_BASELINE, f"t{i}", True, score=1.0) for i in range(4)]
        appr = [_outcome(ARM_APPROVED, f"t{i}", False) for i in range(4)]
        r = compare_paired(base, appr)
        assert r.verdict == VERDICT_FAIL
        assert r.success_delta < 0

    def test_no_difference_is_inconclusive(self):
        base = [_outcome(ARM_BASELINE, f"t{i}", True, score=0.5) for i in range(4)]
        appr = [_outcome(ARM_APPROVED, f"t{i}", True, score=0.5) for i in range(4)]
        r = compare_paired(base, appr)
        assert r.verdict == VERDICT_INCONCLUSIVE

    def test_too_few_pairs_is_inconclusive(self):
        base = [_outcome(ARM_BASELINE, "t1", False)]
        appr = [_outcome(ARM_APPROVED, "t1", True, score=1.0)]
        r = compare_paired(base, appr)
        assert r.verdict == VERDICT_INCONCLUSIVE
        assert r.n_pairs == 1

    def test_only_shared_tasks_are_compared(self):
        base = [_outcome(ARM_BASELINE, f"t{i}", False) for i in range(5)]
        appr = [_outcome(ARM_APPROVED, f"t{i}", True, score=1.0) for i in range(3)]
        r = compare_paired(base, appr)
        # Only t0..t2 are shared -> 3 pairs, still >= MIN_PAIRED_TRIALS.
        assert r.n_pairs == 3

    def test_min_pairs_constant_is_positive(self):
        assert MIN_PAIRED_TRIALS >= 1


# ---------------------------------------------------------------------------
# Trial execution / runner coercion
# ---------------------------------------------------------------------------
class TestTrialExecution:
    def test_runner_dict_is_coerced(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        o = exp._run_trial(ARM_APPROVED, "t1", 1)
        assert o.success is True
        assert o.score == 1.0

    def test_runner_bool_is_coerced(self):
        exp = EffectExperiment("e1", runner=lambda a, t, o: True)
        out = exp._run_trial(ARM_APPROVED, "t1", 1)
        assert out.success is True

    def test_runner_exception_is_recorded_not_raised(self):
        exp = EffectExperiment("e1", runner=_raising_runner)
        out = exp._run_trial(ARM_APPROVED, "t1", 1)
        assert out.success is False
        assert "RuntimeError" in out.error

    def test_no_runner_is_error_trial(self):
        exp = EffectExperiment("e1", runner=None)
        out = exp._run_trial(ARM_APPROVED, "t1", 1)
        assert out.success is False
        assert "no runner" in out.error

    def test_unsupported_return_type_is_error(self):
        exp = EffectExperiment("e1", runner=lambda a, t, o: 12345)
        out = exp._run_trial(ARM_APPROVED, "t1", 1)
        assert out.success is False
        assert "unsupported type" in out.error


# ---------------------------------------------------------------------------
# T03 AB/BA order control
# ---------------------------------------------------------------------------
class TestT03OrderControl:
    def test_t03_runs_four_trials_per_task(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        exp.run_t03(["t1", "t2"])
        # 2 tasks x (A,B,B,A) = 8 trials.
        assert len(exp._trials) == 8

    def test_t03_ab_ba_ordering(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        exp.run_t03(["t1"])
        arms = [t.arm for t in exp._trials]
        assert arms == [ARM_BASELINE, ARM_APPROVED, ARM_APPROVED, ARM_BASELINE]

    def test_t03_improvement_verdict(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t03(["t1", "t2", "t3"])
        assert r.verdict == VERDICT_PASS

    def test_t03_regression_verdict(self):
        exp = EffectExperiment("e1", runner=_regressing_runner)
        r = exp.run_t03(["t1", "t2", "t3"])
        assert r.verdict == VERDICT_FAIL


# ---------------------------------------------------------------------------
# T04~T08
# ---------------------------------------------------------------------------
class TestT04ToT08:
    def test_t04_unseen_pass(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t04(["u1", "u2"])
        assert r["verdict"] == VERDICT_PASS

    def test_t04_no_tasks_inconclusive(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t04([])
        assert r["verdict"] == VERDICT_INCONCLUSIVE

    def test_t05_regression_pass(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t05(["t1", "t2"])
        assert r["verdict"] == VERDICT_PASS

    def test_t05_regression_fail(self):
        exp = EffectExperiment("e1", runner=_regressing_runner)
        r = exp.run_t05(["t1", "t2"])
        assert r["verdict"] == VERDICT_FAIL

    def test_t06_restart_survived(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t06(lambda: True)
        assert r["verdict"] == VERDICT_PASS

    def test_t06_restart_lost(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t06(lambda: False)
        assert r["verdict"] == VERDICT_FAIL

    def test_t06_no_probe_inconclusive(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t06(None)
        assert r["verdict"] == VERDICT_INCONCLUSIVE

    def test_t06_probe_exception_is_error(self):
        exp = EffectExperiment("e1", runner=_improving_runner)

        def boom():
            raise ValueError("nope")

        r = exp.run_t06(boom)
        assert r["verdict"] == VERDICT_ERROR

    def test_t07_rejected_blocked(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t07(lambda: True)
        assert r["verdict"] == VERDICT_PASS

    def test_t07_rejected_leaked(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t07(lambda: False)
        assert r["verdict"] == VERDICT_FAIL

    def test_t08_level_recorded(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t08("B_STATIC_VALIDATION")
        assert r["verdict"] == VERDICT_PASS
        assert r["verification_level"] == "B_STATIC_VALIDATION"

    def test_t08_no_level_inconclusive(self):
        exp = EffectExperiment("e1", runner=_improving_runner)
        r = exp.run_t08("")
        assert r["verdict"] == VERDICT_INCONCLUSIVE


# ---------------------------------------------------------------------------
# Full run + roll-up
# ---------------------------------------------------------------------------
class TestFullRun:
    def _run(self, runner):
        exp = EffectExperiment("exp-001", runner=runner)
        return exp.run(
            baseline_tasks=["t1", "t2", "t3"],
            unseen_tasks=["u1", "u2"],
            restart_probe=lambda: True,
            rejected_blocked=lambda: True,
            verification_level="B_STATIC_VALIDATION",
        )

    def test_full_run_covers_t03_to_t08(self):
        result = self._run(_improving_runner)
        assert set(result.verdicts) == {"T03", "T04", "T05", "T06", "T07", "T08"}

    def test_full_run_all_pass(self):
        result = self._run(_improving_runner)
        assert result.overall_verdict() == VERDICT_PASS

    def test_full_run_records_trials(self):
        result = self._run(_improving_runner)
        assert len(result.trials) > 0
        assert all("arm" in t for t in result.trials)

    def test_overall_rollup_fail_dominates(self):
        result = self._run(_regressing_runner)
        assert result.overall_verdict() == VERDICT_FAIL

    def test_overall_rollup_error_dominates(self):
        result = self._run(_raising_runner)
        assert result.overall_verdict() == VERDICT_ERROR

    def test_overall_rollup_inconclusive(self):
        result = self._run(_flat_runner)
        assert result.overall_verdict() == VERDICT_INCONCLUSIVE

    def test_empty_result_is_inconclusive(self):
        assert ExperimentResult("e").overall_verdict() == VERDICT_INCONCLUSIVE

    def test_result_serializes(self):
        result = self._run(_improving_runner)
        d = result.to_dict()
        assert d["experiment_id"] == "exp-001"
        assert "verdicts" in d and "details" in d


# ---------------------------------------------------------------------------
# Receipt emission
# ---------------------------------------------------------------------------
class TestReceiptEmission:
    def test_emit_maps_verdicts_to_statuses(self):
        exp = EffectExperiment("exp-001", runner=_improving_runner)
        em = EvidenceReceiptEmitter()
        exp.run_and_emit(
            em,
            baseline_tasks=["t1", "t2", "t3"],
            unseen_tasks=["u1", "u2"],
            restart_probe=lambda: True,
            rejected_blocked=lambda: True,
            verification_level="B_STATIC_VALIDATION",
        )
        recorded = {r.test_id: r.status for r in em.receipts}
        assert recorded["T03"] == STATUS_PASS
        assert recorded["T08"] == STATUS_PASS

    def test_emit_inconclusive_becomes_skip(self):
        exp = EffectExperiment("exp-001", runner=_flat_runner)
        em = EvidenceReceiptEmitter()
        exp.run_and_emit(
            em,
            baseline_tasks=["t1", "t2", "t3"],
            verification_level="B_STATIC_VALIDATION",
        )
        recorded = {r.test_id: r.status for r in em.receipts}
        assert recorded["T03"] == STATUS_SKIP

    def test_emit_fail_becomes_fail(self):
        exp = EffectExperiment("exp-001", runner=_regressing_runner)
        em = EvidenceReceiptEmitter()
        exp.run_and_emit(
            em,
            baseline_tasks=["t1", "t2", "t3"],
            verification_level="B_STATIC_VALIDATION",
        )
        recorded = {r.test_id: r.status for r in em.receipts}
        assert recorded["T03"] == STATUS_FAIL

    def test_emit_error_becomes_error(self):
        exp = EffectExperiment("exp-001", runner=_raising_runner)
        em = EvidenceReceiptEmitter()
        exp.run_and_emit(
            em,
            baseline_tasks=["t1", "t2", "t3"],
            verification_level="B_STATIC_VALIDATION",
        )
        recorded = {r.test_id: r.status for r in em.receipts}
        assert recorded["T03"] == STATUS_ERROR

    def test_emitted_receipts_carry_evidence(self):
        exp = EffectExperiment("exp-001", runner=_improving_runner)
        em = EvidenceReceiptEmitter()
        exp.run_and_emit(
            em,
            baseline_tasks=["t1", "t2", "t3"],
            verification_level="B_STATIC_VALIDATION",
        )
        t03 = next(r for r in em.receipts if r.test_id == "T03")
        assert "n_pairs" in t03.evidence
        assert t03.evidence["n_pairs"] == 3


# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------
class TestVerdictVocabulary:
    def test_valid_verdicts(self):
        assert VALID_VERDICTS == {
            VERDICT_PASS,
            VERDICT_FAIL,
            VERDICT_INCONCLUSIVE,
            VERDICT_ERROR,
        }

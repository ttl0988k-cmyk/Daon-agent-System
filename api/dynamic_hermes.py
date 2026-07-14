"""
Backward-compatibility shim for api.dynamic_hermes.

This module has been refactored into the `api.dynamic` package
(api/dynamic/*.py). All public symbols are re-exported from there.

Existing imports like:
    from api.dynamic_hermes import HermesDynamicRunner

will continue to work without modification.
"""

# Re-export everything from the new modular package
from api.dynamic import (  # noqa: F401, E402
    StreamLogBuffer,
    NodeMetrics,
    MissionMetrics,
    StateValue,
    HermesStateManager,
    select_best_output,
    ReviewFailedException,
    _load_harness_limits,
    cleanup_harness_artifacts,
    _get_minimax_api_key,
    _get_deepseek_api_key,
    _call_minimax_direct,
    _call_deepseek_direct,
    _call_direct,
    _extract_assistant_content,
    _get_model_chain_for_node,
    _compress_context,
    _build_dag_structures,
    _compute_execution_batches,
    validate_plan_schema,
    semantic_validate,
    HermesPlanner,
    get_integrated_persona,
    AgentCompiler,
    _build_node_context,
    _persist_node_result,
    _run_node_with_retries,
    _handle_timeout_node,
    ParallelRunner,
    ResultMerger,
    HermesDynamicRunner,
    _extract_and_save_skill,
)

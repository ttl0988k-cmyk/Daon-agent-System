"""
api.dynamic package — modularized Dynamic Hermes execution engine.

Re-exports all public symbols for backward compatibility.
Import this package instead of `api.dynamic_hermes`.
"""

from api.dynamic.state import (
    StreamLogBuffer,
    NodeMetrics,
    MissionMetrics,
    StateValue,
    HermesStateManager,
    select_best_output,
    ReviewFailedException,
)
from api.dynamic.limits import _load_harness_limits, cleanup_harness_artifacts
from api.dynamic.auth import _get_minimax_api_key, _get_deepseek_api_key
from api.dynamic.direct_calls import _call_minimax_direct, _call_deepseek_direct, _call_direct
from api.dynamic.dag_utils import (
    _extract_assistant_content,
    _get_model_chain_for_node,
    _compress_context,
    _build_dag_structures,
    _compute_execution_batches,
)
from api.dynamic.plan_validator import validate_plan_schema, semantic_validate
from api.dynamic.planner import HermesPlanner
from api.dynamic.compiler import get_integrated_persona, AgentCompiler
from api.dynamic.runner import (
    _build_node_context,
    _persist_node_result,
    _run_node_with_retries,
    _handle_timeout_node,
    ParallelRunner,
)
from api.dynamic.merger import ResultMerger
from api.dynamic.orchestrator import HermesDynamicRunner
from api.dynamic.skill_extractor import _extract_and_save_skill
from api.dynamic.skill_retriever import SemanticSkillRetriever, get_skill_retriever
from api.dynamic.model_selector import DynamicModelSelector, get_model_selector, ModelProfile, ModelHistory, SkillHistory, get_skill_history
from api.dynamic.experience_db import ExperienceDatabase, get_experience_db

__all__ = [
    "StreamLogBuffer",
    "NodeMetrics",
    "MissionMetrics",
    "StateValue",
    "HermesStateManager",
    "select_best_output",
    "ReviewFailedException",
    "_load_harness_limits",
    "cleanup_harness_artifacts",
    "_get_minimax_api_key",
    "_get_deepseek_api_key",
    "_call_minimax_direct",
    "_call_deepseek_direct",
    "_call_direct",
    "_extract_assistant_content",
    "_get_model_chain_for_node",
    "_compress_context",
    "_build_dag_structures",
    "_compute_execution_batches",
    "validate_plan_schema",
    "semantic_validate",
    "HermesPlanner",
    "get_integrated_persona",
    "AgentCompiler",
    "_build_node_context",
    "_persist_node_result",
    "_run_node_with_retries",
    "_handle_timeout_node",
    "ParallelRunner",
    "ResultMerger",
    "HermesDynamicRunner",
    "_extract_and_save_skill",
    "SemanticSkillRetriever",
    "get_skill_retriever",
    "DynamicModelSelector",
    "get_model_selector",
    "ModelProfile",
    "ModelHistory",
    "SkillHistory",
    "get_skill_history",
    "ExperienceDatabase",
    "get_experience_db",
]

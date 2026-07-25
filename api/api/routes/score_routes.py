"""
Score API Routes for Daon Agent System.
Provides the GET /api/score/evaluate endpoint for configuration completeness scoring.
"""

import logging
from api.helpers import j_ok, j_err
from api.score_engine import evaluate_config_score

_logger = logging.getLogger(__name__)


def handle_get_score_evaluate(handler, parsed) -> bool:
    """GET /api/score/evaluate — evaluate configuration completeness (0-100)."""
    try:
        result = evaluate_config_score()
        return j_ok(handler, result)
    except Exception as e:
        _logger.error("Failed to evaluate config score: %s", e, exc_info=True)
        return j_err(handler, f"설정 평가 중 오류 발생: {str(e)}")

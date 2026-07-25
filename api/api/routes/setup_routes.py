"""
Setup Routes for Daon Agent System.
Provides API endpoints for AI setup file generation (AGENTS.md, CLAUDE.md, etc.).
"""

import json
import logging
from urllib.parse import parse_qs

from api.helpers import j_ok, j_err
from api.setup_generator import (
    preview_setup_file,
    generate_setup_files,
    FILE_TYPE_LABELS,
)

_logger = logging.getLogger(__name__)


def handle_get_setup_preview(handler, parsed) -> bool:
    """GET /api/setup/preview?workspace=...&file_type=agents.md

    Preview a single setup file without writing to disk.
    """
    try:
        query = parse_qs(parsed.query)
        workspace = query.get("workspace", [None])[0]
        file_type = query.get("file_type", ["agents.md"])[0]

        if not workspace:
            return j_err(handler, "workspace 파라미터가 필요합니다")

        result = preview_setup_file(workspace, file_type)
        if result.get("error"):
            return j_err(handler, result["error"])

        return j_ok(handler, {
            "content": result["content"],
            "filename": result["filename"],
            "will_overwrite": result["will_overwrite"],
        })
    except Exception as e:
        _logger.error("Setup preview failed: %s", e, exc_info=True)
        return j_err(handler, f"미리보기 생성 중 오류: {str(e)}")


def handle_post_setup_generate(handler, body: dict) -> bool:
    """POST /api/setup/generate

    Body: {
        "workspace": "/path/to/project",
        "file_types": ["agents.md", "claude.md", "cursor_rules", "copilot_instructions"],
        "overwrite": false
    }
    """
    try:
        workspace = body.get("workspace")
        file_types = body.get("file_types", ["agents.md"])
        overwrite = body.get("overwrite", False)

        if not workspace:
            return j_err(handler, "workspace 필드가 필요합니다")

        if not isinstance(file_types, list) or len(file_types) == 0:
            return j_err(handler, "file_types는 하나 이상의 타입을 포함해야 합니다")

        # Validate file types
        valid_types = set(FILE_TYPE_LABELS.keys())
        invalid = [ft for ft in file_types if ft not in valid_types]
        if invalid:
            return j_err(handler, f"지원하지 않는 파일 타입: {', '.join(invalid)}")

        result = generate_setup_files(workspace, file_types, overwrite)

        # Build user-friendly messages
        generated_labels = [FILE_TYPE_LABELS.get(ft, ft) for ft in result["generated"]]
        skipped_labels = [FILE_TYPE_LABELS.get(ft, ft) for ft in result["skipped"]]
        error_msgs = [f"{e['file']}: {e['error']}" for e in result["errors"]]

        return j_ok(handler, {
            "generated": result["generated"],
            "generated_labels": generated_labels,
            "skipped": result["skipped"],
            "skipped_labels": skipped_labels,
            "errors": result["errors"],
            "error_messages": error_msgs,
            "workspace": result["workspace"],
            "tech_stack": result.get("tech_stack"),
        })
    except Exception as e:
        _logger.error("Setup generation failed: %s", e, exc_info=True)
        return j_err(handler, f"설정 파일 생성 중 오류: {str(e)}")


def handle_get_setup_detect(handler, parsed) -> bool:
    """GET /api/setup/detect?workspace=...

    Detect the tech stack of a given workspace without generating files.
    """
    try:
        query = parse_qs(parsed.query)
        workspace = query.get("workspace", [None])[0]

        if not workspace:
            return j_err(handler, "workspace 파라미터가 필요합니다")

        # Use preview to get structure analysis
        result = preview_setup_file(workspace, "agents.md")
        if result.get("error"):
            return j_err(handler, result["error"])

        # The tech stack is detected but not returned in preview yet,
        # so we do a lightweight detection
        from api.setup_generator import _discover_project_structure, _detect_tech_stack
        from pathlib import Path

        ws_path = Path(workspace).resolve()
        structure = _discover_project_structure(ws_path)
        tech_stack = _detect_tech_stack(ws_path, structure)

        return j_ok(handler, {
            "workspace": str(ws_path),
            "tech_stack": tech_stack,
            "file_count": structure.get("total_files", 0),
        })
    except Exception as e:
        _logger.error("Setup detection failed: %s", e, exc_info=True)
        return j_err(handler, f"기술 스택 감지 중 오류: {str(e)}")

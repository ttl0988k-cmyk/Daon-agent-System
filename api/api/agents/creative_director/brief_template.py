"""Brief 템플릿 로더. Markdown 템플릿 파일을 읽어서 string 으로 반환."""
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).parent / "brief_template.md"


def load_template() -> str:
    """Brief 템플릿 Markdown 을 읽어 반환. 파일이 없으면 placeholder 반환."""
    if _TEMPLATE_PATH.exists():
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    return "# Design Brief\n\n## (Template not found)"
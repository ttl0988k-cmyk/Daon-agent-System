"""One-time script: rebuild _catalog.yaml to v1.2 with cost_tier field."""
import yaml
from pathlib import Path

AGENTS_DIR = Path(__file__).parent
OUTPUT = AGENTS_DIR / "_catalog.yaml"

CATEGORY_DESCRIPTIONS = {
    "developer": "개발 전문 에이전트 (언어/프레임워크별)",
    "reviewer": "코드 리뷰 및 감사 전문 에이전트",
    "qa": "테스트 및 품질 보증 전문 에이전트",
    "designer": "UI/UX 디자인 전문 에이전트",
    "planner": "아키텍처 및 프로젝트 계획 전문 에이전트",
    "debugger": "디버깅 및 문제 진단 전문 에이전트",
    "specialist": "특수 분야 전문 에이전트",
    "writer": "문서 및 콘텐츠 작성 전문 에이전트",
    "integrator": "시스템 통합 및 자동화 전문 에이전트",
}

def main():
    categories = {}
    total = 0

    for yaml_file in sorted(AGENTS_DIR.rglob("*.yaml")):
        if yaml_file.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data or "id" not in data:
            continue

        cat = data.get("category", "specialist")
        if cat not in categories:
            categories[cat] = []

        entry = {
            "id": data["id"],
            "display_name": data.get("display_name", data["id"]),
            "tags": data.get("tags", []),
            "capability": data.get("capability", ""),
            "domain": data.get("domain", ""),
            "avoid_when": data.get("avoid_when", []),
            "cost_tier": (data.get("cost_profile") or {}).get("tier", "mid"),
        }
        categories[cat].append(entry)
        total += 1

    # Build output structure
    out = {
        "version": "1.2",
        "total_templates": total,
        "categories": {},
    }
    for cat_name in sorted(categories.keys()):
        out["categories"][cat_name] = {
            "description": CATEGORY_DESCRIPTIONS.get(cat_name, f"{cat_name} 에이전트"),
            "templates": categories[cat_name],
        }

    header = (
        "# Agent Template Catalog v1.2\n"
        "# CEO 에이전트가 이 카탈로그에서 템플릿 ID를 선택하여 DAG를 구성합니다.\n"
        f"# 총 {total}개 템플릿, {len(categories)}개 카테고리\n"
        "# v1.2: cost_tier 필드 추가 (low/mid/high) — 비용 최적화 의사결정용\n"
        "\n"
    )

    yaml_str = yaml.dump(out, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)
    OUTPUT.write_text(header + yaml_str, encoding="utf-8")
    print(f"Done: {total} templates, {len(categories)} categories -> {OUTPUT.name}")

if __name__ == "__main__":
    main()

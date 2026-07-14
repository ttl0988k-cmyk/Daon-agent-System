"""
Style Card — 메타데이터 기반 디자인 레퍼런스 데이터 구조.

Style Card는 이미지가 아닌 구조화된 YAML 메타데이터로 디자인 레퍼런스를 표현한다.
Design DNA (색상, 타이포그래피, 레이아웃, 애니메이션) + DO/DON'T 가이드라인을 포함하며,
컴포넌트 단위(hero, navbar, gallery, footer 등)로 분류·검색된다.

v2.0 — ComponentCard (Knowledge Graph 단위) + StyleCard.decomposed_cards 추가.
        Style Mixing 을 위한 컴포넌트 레벨 분해 지원.

Usage:
    card = StyleCard.from_yaml(path)
    card.evaluate()  # → float score
    card.to_brief_text()  # → Design Brief 용 텍스트
    card.to_search_document()  # → TF-IDF 검색용 평문
    components = card.decompose_to_components()  # → list[ComponentCard]
"""

from __future__ import annotations

import json as _json
import uuid as _uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── YAML import (fallback to JSON) ─────────────────────────────────────────
try:
    import yaml as _yaml

    def _yaml_load(fp):
        return _yaml.safe_load(fp)

    def _yaml_dump(data, fp):
        return _yaml.safe_dump(data, fp, allow_unicode=True, sort_keys=False, default_flow_style=False)
except ImportError:
    _json_load = _json.load
    _json_dump = _json.dump

    def _yaml_load(fp):
        return _json_load(fp)

    def _yaml_dump(data, fp):
        return _json_dump(data, fp, ensure_ascii=False, indent=2)


# ── Design DNA sub-structures ──────────────────────────────────────────────

@dataclass
class ColorDNA:
    """색상 시스템."""
    primary: str = "#6366F1"
    accent: str = "#06B6D4"
    background: str = "#0F172A"
    surface: str = "rgba(30,41,59,0.6)"
    text_primary: str = "#F8FAFC"
    text_secondary: str = "#94A3B8"
    palette_name: str = "Midnight Aurora"
    palette_harmony: str = "complementary"  # complementary | analogous | triadic | monochromatic


@dataclass
class TypographyDNA:
    """타이포그래피 시스템."""
    heading_font: str = "Inter"
    body_font: str = "Inter"
    mono_font: str = "JetBrains Mono"
    scale: str = "minor-third"  # major-second | minor-second | major-third | perfect-fourth
    heading_weight: int = 700
    body_weight: int = 400
    letter_spacing_heading: str = "-0.03em"
    line_height_body: float = 1.6


@dataclass
class LayoutDNA:
    """레이아웃 시스템."""
    grid: str = "12-column"  # 12-column | bento | masonry | asymmetric | single-column | split
    max_width: str = "1280px"
    padding_desktop: str = "80px"
    padding_mobile: str = "24px"
    alignment: str = "center"
    glass_effect: bool = False
    border_radius: str = "16px"
    backdrop_blur: str = "0px"


@dataclass
class AnimationDNA:
    """애니메이션 시스템."""
    entrance: str = "fade-up"  # fade-up | scale-in | slide-left | reveal | stagger
    hover: str = "scale-105"
    scroll: str = "parallax"
    page_transition: str = "crossfade"
    duration_base: str = "400ms"
    easing: str = "cubic-bezier(0.16, 1, 0.3, 1)"  # spring-out
    motion_intensity: int = 3  # 1-10


@dataclass
class SpacingDNA:
    """공간감 시스템."""
    density: str = "airy"  # compact | moderate | airy | luxurious
    section_gap: str = "120px"
    element_gap: str = "24px"


@dataclass
class DesignDNA:
    """통합 Design DNA."""
    colors: ColorDNA = field(default_factory=ColorDNA)
    typography: TypographyDNA = field(default_factory=TypographyDNA)
    layout: LayoutDNA = field(default_factory=LayoutDNA)
    animation: AnimationDNA = field(default_factory=AnimationDNA)
    spacing: SpacingDNA = field(default_factory=SpacingDNA)


@dataclass
class Composition:
    """컴포넌트 구성 규칙 (순서 있는 구조 리스트)."""
    structure: list[str] = field(default_factory=list)


@dataclass
class Guidelines:
    """DO / DON'T 가이드라인."""
    do: list[str] = field(default_factory=list)
    dont: list[str] = field(default_factory=list)


@dataclass
class Evaluation:
    """스타일 카드 평가 점수."""
    score: float = 0.0  # 1-10 total
    originality: float = 0.0
    accessibility: float = 0.0
    responsiveness: float = 0.0
    trend_relevance: str = "medium"  # low | medium | high
    reviewed: bool = False
    review_date: str = ""


# ── Component Card (Knowledge Graph 단위) ──────────────────────────────────

# 유효한 카테고리 목록
VALID_COMPONENT_CATEGORIES = frozenset({
    "hero", "navbar", "header", "cta", "footer", "card", "modal",
    "gallery", "features", "pricing", "testimonial", "contact",
    "sidebar", "breadcrumb", "pagination", "tabs", "accordion",
    "carousel", "tooltip", "badge", "avatar", "button",
    "typography", "color", "spacing", "animation", "icon",
    "input", "form", "table", "chart", "timeline", "stats",
})


@dataclass
class ComponentCard:
    """
    단일 컴포넌트 패턴을 표현하는 Knowledge Graph 단위.

    StyleCard(홀리스틱 레퍼런스)에서 분해되어 생성되며,
    컴포넌트 단위 검색·랭킹·Style Mixing 에 사용된다.

    StyleCard 와 동일한 DesignDNA 구조를 가지지만,
    category 는 단일 컴포넌트 유형으로 고정된다.
    """

    # ── 식별자 ──
    id: str = ""
    name: str = ""
    parent_card_id: str = ""  # 원본 StyleCard.id
    source_url: str = ""
    created: str = ""

    # ── 분류 ──
    category: str = ""  # VALID_COMPONENT_CATEGORIES 중 하나
    sub_category: str = ""  # e.g., hero: "일본감성", "미니멀", "빈티지"
    tags: list[str] = field(default_factory=list)

    # ── 디자인 DNA ──
    design_dna: DesignDNA = field(default_factory=DesignDNA)

    # ── 구성 규칙 ──
    composition: Composition = field(default_factory=Composition)

    # ── DO / DON'T ──
    guidelines: Guidelines = field(default_factory=Guidelines)

    # ── 호환성 (Style Mixing 용) ──
    compatible_categories: list[str] = field(default_factory=list)
    conflicts_with_categories: list[str] = field(default_factory=list)
    harmony_score: float = 0.0  # 1-10, 이 컴포넌트가 다른 컴포넌트와 조화될 가능성

    # ── 평가 ──
    evaluation: Evaluation = field(default_factory=Evaluation)

    def __post_init__(self):
        """None 방어: design_dna가 명시적으로 None으로 설정된 경우 기본값 할당."""
        if self.design_dna is None:
            self.design_dna = DesignDNA()

    def to_search_document(self) -> str:
        """TF-IDF 검색용 평문을 생성한다."""
        dna = self.design_dna
        parts = [
            self.name,
            self.category,
            self.sub_category,
            dna.colors.palette_name,
            dna.typography.heading_font,
            dna.typography.scale,
            dna.layout.grid,
            dna.animation.entrance,
            dna.spacing.density,
            " ".join(self.tags),
            " ".join(self.guidelines.do),
            " ".join(self.guidelines.dont),
        ]
        return " ".join(p for p in parts if p)

    def to_brief_text(self) -> str:
        """Design Brief 용 컴포넌트 요약 텍스트."""
        dna = self.design_dna
        lines = [
            f"#### [{self.category.upper()}] {self.name} — Score: {self.evaluation.score}/10",
            f"- **Palette**: {dna.colors.palette_name} | Primary: `{dna.colors.primary}`",
            f"- **Type**: {dna.typography.heading_font} ({dna.typography.scale})",
            f"- **Layout**: {dna.layout.grid}, {dna.spacing.density}",
            f"- **Motion**: {dna.animation.entrance}, intensity {dna.animation.motion_intensity}/10",
        ]
        if self.guidelines.do:
            lines.append(f"- **DO**: {'; '.join(self.guidelines.do[:2])}")
        if self.guidelines.dont:
            lines.append(f"- **DON'T**: {'; '.join(self.guidelines.dont[:2])}")
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """ComponentCard를 dict로 직렬화."""
        dna = self.design_dna or DesignDNA()
        return {
            "id": self.id,
            "name": self.name,
            "parent_card_id": self.parent_card_id,
            "source_url": self.source_url,
            "created": self.created,
            "category": self.category,
            "sub_category": self.sub_category,
            "tags": self.tags,
            "design_dna": {
                "colors": asdict(dna.colors),
                "typography": asdict(dna.typography),
                "layout": asdict(dna.layout),
                "animation": asdict(dna.animation),
                "spacing": asdict(dna.spacing),
            },
            "composition": {
                "structure": self.composition.structure,
            },
            "guidelines": {
                "do": self.guidelines.do,
                "dont": self.guidelines.dont,
            },
            "compatible_categories": self.compatible_categories,
            "conflicts_with_categories": self.conflicts_with_categories,
            "harmony_score": self.harmony_score,
            "evaluation": asdict(self.evaluation),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ComponentCard":
        """dict에서 ComponentCard를 생성한다."""
        def _pop(d: dict, key: str, default=None):
            val = d.pop(key, default)
            return val if val is not None else default

        design_dna_raw = _pop(data, "design_dna", {}) or {}
        colors = ColorDNA(**_pop(design_dna_raw, "colors", {}) or {})
        typography = TypographyDNA(**_pop(design_dna_raw, "typography", {}) or {})
        layout = LayoutDNA(**_pop(design_dna_raw, "layout", {}) or {})
        animation = AnimationDNA(**_pop(design_dna_raw, "animation", {}) or {})
        spacing = SpacingDNA(**_pop(design_dna_raw, "spacing", {}) or {})

        composition_raw = _pop(data, "composition", {}) or {}
        composition = Composition(structure=composition_raw.get("structure", []))

        guidelines_raw = _pop(data, "guidelines", {}) or {}
        guidelines = Guidelines(
            do=guidelines_raw.get("do", []),
            dont=guidelines_raw.get("dont", []),
        )

        evaluation_raw = _pop(data, "evaluation", {}) or {}
        evaluation = Evaluation(**evaluation_raw)

        return cls(
            id=_pop(data, "id", ""),
            name=_pop(data, "name", ""),
            parent_card_id=_pop(data, "parent_card_id", ""),
            source_url=_pop(data, "source_url", ""),
            created=_pop(data, "created", ""),
            category=_pop(data, "category", ""),
            sub_category=_pop(data, "sub_category", ""),
            tags=_pop(data, "tags", []) or [],
            design_dna=DesignDNA(colors=colors, typography=typography, layout=layout, animation=animation, spacing=spacing),
            composition=composition,
            guidelines=guidelines,
            compatible_categories=_pop(data, "compatible_categories", []) or [],
            conflicts_with_categories=_pop(data, "conflicts_with_categories", []) or [],
            harmony_score=_pop(data, "harmony_score", 0.0),
            evaluation=evaluation,
        )

    def evaluate(self) -> float:
        """ComponentCard 종합 점수를 계산한다 (StyleCard.evaluate 와 동일 로직)."""
        trend_map = {"low": 3.0, "medium": 6.0, "high": 9.0}
        trend_score = trend_map.get(self.evaluation.trend_relevance, 5.0)
        total = (
            self.evaluation.originality * 0.35
            + self.evaluation.accessibility * 0.25
            + self.evaluation.responsiveness * 0.25
            + trend_score * 0.15
        )
        self.evaluation.score = round(total, 1)
        self.evaluation.reviewed = True
        self.evaluation.review_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.evaluation.score

    def __repr__(self) -> str:
        return f"ComponentCard(id={self.id!r}, name={self.name!r}, category={self.category!r}, score={self.evaluation.score})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, ComponentCard):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


# ── Style Card ─────────────────────────────────────────────────────────────

@dataclass
class StyleCard:
    """
    단일 디자인 레퍼런스를 표현하는 메타데이터 카드.

    YAML 직렬화/역직렬화를 지원하며, 컴포넌트 단위로 분류되어
    SemanticSkillRetriever 와 동일한 TF-IDF 백엔드로 검색 가능하다.

    v2.0: decomposed_cards 필드 추가 — Style Mixing 을 위한
          컴포넌트 레벨 분해 (ComponentCard 목록).
    """

    # ── 식별자 ──
    id: str = ""
    name: str = ""
    version: str = "1.0.0"
    created: str = ""
    source_url: str = ""
    source_name: str = ""  # human-readable source identity (e.g. "Apple", "Stripe", "Linear", "Notion")
    source_type: str = "manual"  # manual | mcp_collected | user_submitted | demo_extracted

    # ── 분류 ──
    category: str = ""  # hero, navbar, gallery, features, pricing, cta, footer, card, modal, ...
    sub_category: str = ""
    tags: list[str] = field(default_factory=list)

    # ── 디자인 DNA ──
    design_dna: DesignDNA = field(default_factory=DesignDNA)

    # ── 구성 규칙 ──
    composition: Composition = field(default_factory=Composition)

    # ── DO / DON'T ──
    guidelines: Guidelines = field(default_factory=Guidelines)

    # ── 호환성 ──
    compatible_with: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)

    # ── 평가 ──
    evaluation: Evaluation = field(default_factory=Evaluation)

    # ── 파일 경로 (저장 시 자동 설정) ──
    _file_path: Optional[Path] = field(default=None, repr=False)

    # ── Knowledge Graph: decomposed components (v2.0) ──
    decomposed_cards: list[ComponentCard] = field(default_factory=list)

    def __post_init__(self):
        """None 방어: design_dna가 명시적으로 None으로 설정된 경우 기본값 할당."""
        if self.design_dna is None:
            self.design_dna = DesignDNA()
        if self.decomposed_cards is None:
            self.decomposed_cards = []

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: Path) -> "StyleCard":
        """YAML 파일에서 StyleCard를 로드한다."""
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml_load(f) or {}

        # 최상위 style_card 키 처리
        if "style_card" in data:
            data = data["style_card"]

        card = cls._from_dict(data)
        card._file_path = path

        # 파일명에서 ID 자동 추출
        if not card.id:
            card.id = path.stem

        return card

    @classmethod
    def from_dict(cls, data: dict) -> "StyleCard":
        """dict에서 StyleCard를 생성한다."""
        # 최상위 style_card 키 처리
        if "style_card" in data:
            data = data["style_card"]
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "StyleCard":
        """내부 dict→StyleCard 변환 (재귀적 dataclass 복원)."""

        def _pop_dict(d: dict, key: str, default=None):
            val = d.pop(key, default)
            return val if val is not None else default

        design_dna_raw = _pop_dict(data, "design_dna", {}) or {}
        colors = ColorDNA(**_pop_dict(design_dna_raw, "colors", {}) or {})
        typography = TypographyDNA(**_pop_dict(design_dna_raw, "typography", {}) or {})
        layout = LayoutDNA(**_pop_dict(design_dna_raw, "layout", {}) or {})
        animation = AnimationDNA(**_pop_dict(design_dna_raw, "animation", {}) or {})
        spacing = SpacingDNA(**_pop_dict(design_dna_raw, "spacing", {}) or {})

        composition_raw = _pop_dict(data, "composition", {}) or {}
        composition = Composition(structure=composition_raw.get("hero_structure", composition_raw.get("structure", [])))

        guidelines_raw = _pop_dict(data, "guidelines", {}) or {}
        guidelines = Guidelines(
            do=guidelines_raw.get("do", []),
            dont=guidelines_raw.get("dont", []),
        )

        evaluation_raw = _pop_dict(data, "evaluation", {}) or {}
        evaluation = Evaluation(**evaluation_raw)

        # ── v2.0: decomposed_cards 역직렬화 ──
        decomposed_raw = _pop_dict(data, "decomposed_cards", []) or []
        decomposed_cards = []
        for comp_data in decomposed_raw:
            try:
                decomposed_cards.append(ComponentCard.from_dict(comp_data))
            except Exception:
                pass

        return cls(
            id=_pop_dict(data, "id", ""),
            name=_pop_dict(data, "name", ""),
            version=_pop_dict(data, "version", "1.0.0"),
            created=_pop_dict(data, "created", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            source_url=_pop_dict(data, "source_url", ""),
            source_name=_pop_dict(data, "source_name", ""),
            source_type=_pop_dict(data, "source_type", "manual"),
            category=_pop_dict(data, "category", ""),
            sub_category=_pop_dict(data, "sub_category", ""),
            tags=_pop_dict(data, "tags", []) or [],
            design_dna=DesignDNA(colors=colors, typography=typography, layout=layout, animation=animation, spacing=spacing),
            composition=composition,
            guidelines=guidelines,
            compatible_with=_pop_dict(data, "compatible_with", []) or [],
            conflicts_with=_pop_dict(data, "conflicts_with", []) or [],
            evaluation=evaluation,
            decomposed_cards=decomposed_cards,
        )

    # ── Serialization ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """StyleCard를 dict로 직렬화 (YAML/JSON 저장용)."""
        dna = self.design_dna or DesignDNA()
        decomposed = [c.to_dict() for c in (self.decomposed_cards or [])]
        return {
            "style_card": {
                "id": self.id,
                "name": self.name,
                "version": self.version,
                "created": self.created,
                "source_url": self.source_url,
                "source_name": self.source_name,
                "source_type": self.source_type,
                "category": self.category,
                "sub_category": self.sub_category,
                "tags": self.tags,
                "design_dna": {
                    "colors": asdict(dna.colors),
                    "typography": asdict(dna.typography),
                    "layout": asdict(dna.layout),
                    "animation": asdict(dna.animation),
                    "spacing": asdict(dna.spacing),
                },
                "composition": {
                    "structure": self.composition.structure,
                },
                "guidelines": {
                    "do": self.guidelines.do,
                    "dont": self.guidelines.dont,
                },
                "compatible_with": self.compatible_with,
                "conflicts_with": self.conflicts_with,
                "evaluation": asdict(self.evaluation),
                "decomposed_cards": decomposed,
            }
        }

    def to_yaml(self, path: Optional[Path] = None) -> Optional[str]:
        """StyleCard를 YAML 문자열로 직렬화. path가 주어지면 파일로 저장."""
        yaml_str = _yaml_dump(self.to_dict(), None)
        # _yaml_dump returns None when fp is None (our fallback), so build manually
        if yaml_str is None:
            import io
            buf = io.StringIO()
            _yaml_dump(self.to_dict(), buf)
            yaml_str = buf.getvalue()

        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(yaml_str)
            self._file_path = path

        return yaml_str

    def save(self, base_dir: Path) -> Path:
        """지정된 base_dir 아래 category/ 디렉토리에 저장. 파일 경로를 반환."""
        if not self.id:
            self.id = f"{self.category or 'general'}-{_uuid.uuid4().hex[:6]}"
        if not self.created:
            self.created = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        category_dir = base_dir / (self.category or "general")
        category_dir.mkdir(parents=True, exist_ok=True)
        file_path = category_dir / f"{self.id}.yaml"
        self.to_yaml(file_path)
        return file_path

    # ── Evaluation ─────────────────────────────────────────────────────────

    def evaluate(self) -> float:
        """
        Style Card 종합 점수를 계산하고 evaluation.score에 반영한다.
        가중치: originality(35%) + accessibility(25%) + responsiveness(25%) + trend(15%)
        """
        trend_map = {"low": 3.0, "medium": 6.0, "high": 9.0}
        trend_score = trend_map.get(self.evaluation.trend_relevance, 5.0)

        total = (
            self.evaluation.originality * 0.35
            + self.evaluation.accessibility * 0.25
            + self.evaluation.responsiveness * 0.25
            + trend_score * 0.15
        )
        self.evaluation.score = round(total, 1)
        self.evaluation.reviewed = True
        self.evaluation.review_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.evaluation.score

    # ── Search / Brief ─────────────────────────────────────────────────────

    def to_search_document(self) -> str:
        """
        TF-IDF 검색용 평문을 생성한다.
        name + category + tags + guidelines.do + guidelines.dont 를 하나의 문서로.
        """
        parts = [
            self.name,
            self.category,
            self.sub_category,
            self.design_dna.colors.palette_name,
            self.design_dna.typography.heading_font,
            self.design_dna.typography.scale,
            self.design_dna.layout.grid,
            self.design_dna.animation.entrance,
            self.design_dna.spacing.density,
            " ".join(self.tags),
            " ".join(self.guidelines.do),
            " ".join(self.guidelines.dont),
        ]
        return " ".join(p for p in parts if p)

    def to_brief_text(self) -> str:
        """
        Design Brief 생성을 위한 요약 텍스트.
        Creative Director가 최종 브리프에 포함시킬 때 사용.
        """
        dna = self.design_dna
        lines = [
            f"### {self.name} ({self.category}) — Score: {self.evaluation.score}/10",
            f"- **Palette**: {dna.colors.palette_name} ({dna.colors.palette_harmony})",
            f"- **Primary**: `{dna.colors.primary}` | Accent: `{dna.colors.accent}` | BG: `{dna.colors.background}`",
            f"- **Typography**: {dna.typography.heading_font} ({dna.typography.scale} scale, H{dna.typography.heading_weight})",
            f"- **Layout**: {dna.layout.grid}, {dna.layout.max_width} max, {dna.spacing.density} density",
            f"- **Animation**: {dna.animation.entrance} entrance, {dna.animation.easing}, intensity {dna.animation.motion_intensity}/10",
            f"- **Glass**: {'Yes' if dna.layout.glass_effect else 'No'} | Radius: {dna.layout.border_radius}",
        ]
        if self.guidelines.do:
            lines.append(f"- **DO**: {'; '.join(self.guidelines.do[:3])}")
        if self.guidelines.dont:
            lines.append(f"- **DON'T**: {'; '.join(self.guidelines.dont[:3])}")
        lines.append("")
        return "\n".join(lines)

    def to_inline_style_hint(self) -> str:
        """
        프롬프트 인젝션용 한 줄 힌트.
        일반 에이전트 모드에서 SKILL.md 없이도 빠르게 스타일 적용할 때 사용.
        """
        dna = self.design_dna
        return (
            f"[Style: {self.name}] Palette={dna.colors.palette_name}, "
            f"Font={dna.typography.heading_font}, "
            f"Grid={dna.layout.grid}, "
            f"Motion={dna.animation.entrance}/{dna.animation.easing}, "
            f"Score={self.evaluation.score}"
        )

    # ── Component Decomposition (v2.0) ─────────────────────────────────────

    def decompose_to_components(self) -> list[ComponentCard]:
        """
        StyleCard를 구성 컴포넌트들로 분해한다.

        이미 decomposed_cards 가 있으면 그대로 반환하고,
        없으면 category와 design_dna를 기반으로 단일 ComponentCard를 생성한다.

        이 메서드는 Style Mixing 파이프라인의 진입점이다.
        """
        if self.decomposed_cards:
            return list(self.decomposed_cards)

        # 수동 분해: 단일 카테고리 → 단일 ComponentCard
        cat = self.category or "general"
        comp = ComponentCard(
            id=f"{self.id}__{cat}",
            name=f"{self.name} ({cat})",
            parent_card_id=self.id,
            source_url=self.source_url,
            created=self.created or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            category=cat,
            sub_category=self.sub_category,
            tags=list(self.tags),
            design_dna=self.design_dna,
            composition=self.composition,
            guidelines=self.guidelines,
            compatible_categories=list(self.compatible_with),
            evaluation=self.evaluation,
        )
        self.decomposed_cards = [comp]
        return list(self.decomposed_cards)

    # ── Magic ──────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        n_decomposed = len(self.decomposed_cards or [])
        base = f"StyleCard(id={self.id!r}, name={self.name!r}, category={self.category!r}, score={self.evaluation.score}"
        if n_decomposed > 0:
            base += f", decomposed={n_decomposed})"
        else:
            base += ")"
        return base

    def __eq__(self, other) -> bool:
        if not isinstance(other, StyleCard):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


# ── Utility: resolve references directory ──────────────────────────────────

def get_references_dir(profile_name: Optional[str] = None) -> Path:
    """
    활성 프로필의 references 디렉토리 경로를 반환한다.

    프로필 시스템과 동일한 로직으로:
    - non-default 프로필 → ~/.hermes/profiles/<name>/references/
    - default → ~/.hermes/references/
    """
    from pathlib import Path as _Path

    if profile_name is None:
        try:
            from api.profiles import get_active_profile_name
            profile_name = get_active_profile_name()
        except Exception:
            profile_name = "default"

    hermes_home = _Path.home() / ".hermes"

    if profile_name and profile_name != "default":
        return hermes_home / "profiles" / profile_name / "references"
    else:
        return hermes_home / "references"


def get_references_index_path(profile_name: Optional[str] = None) -> Path:
    """references/index.yaml 경로를 반환한다."""
    return get_references_dir(profile_name) / "index.yaml"


# ── Registry singleton ─────────────────────────────────────────────────────

class StyleCardRegistry:
    """
    모든 Style Card의 인메모리 레지스트리.
    SkillRegistry 와 동일한 패턴으로, category별 인덱싱 + 태그 검색을 제공한다.
    """

    def __init__(self):
        self._cards: dict[str, StyleCard] = {}  # id → StyleCard
        self._by_category: dict[str, list[str]] = {}  # category → [id, ...]
        self._base_dir: Optional[Path] = None

    def load_all(self, base_dir: Optional[Path] = None) -> int:
        """references 디렉토리에서 모든 Style Card를 로드한다."""
        if base_dir is None:
            base_dir = get_references_dir()
        self._base_dir = base_dir
        self._cards.clear()
        self._by_category.clear()

        if not base_dir.exists():
            return 0

        count = 0
        for yaml_file in base_dir.rglob("*.yaml"):
            if yaml_file.name == "index.yaml":
                continue
            try:
                card = StyleCard.from_yaml(yaml_file)
                self._cards[card.id] = card
                self._by_category.setdefault(card.category, []).append(card.id)
                count += 1
            except Exception:
                pass

        return count

    def get(self, card_id: str) -> Optional[StyleCard]:
        """ID로 Style Card 조회."""
        return self._cards.get(card_id)

    def get_by_category(self, category: str) -> list[StyleCard]:
        """카테고리로 Style Card 목록 조회."""
        ids = self._by_category.get(category, [])
        return [self._cards[cid] for cid in ids if cid in self._cards]

    def get_all_categories(self) -> list[str]:
        """등록된 모든 카테고리 목록."""
        return sorted(self._by_category.keys())

    def add(self, card: StyleCard, save: bool = True) -> bool:
        """Style Card를 레지스트리에 추가하고 (옵션) 파일로 저장."""
        if card.id in self._cards:
            return False
        self._cards[card.id] = card
        self._by_category.setdefault(card.category, []).append(card.id)
        if save and self._base_dir:
            card.save(self._base_dir)
        return True

    def remove(self, card_id: str) -> bool:
        """Style Card를 레지스트리에서 제거."""
        card = self._cards.pop(card_id, None)
        if card is None:
            return False
        self._by_category.get(card.category, []).remove(card_id)
        return True

    def search_by_tags(self, tags: list[str]) -> list[StyleCard]:
        """태그로 Style Card 검색 (AND 조건)."""
        tag_set = set(t.lower() for t in tags)
        results = []
        for card in self._cards.values():
            card_tags = set(t.lower() for t in card.tags)
            if tag_set.issubset(card_tags):
                results.append(card)
        return results

    def rebuild_index(self) -> Path:
        """references/index.yaml 인덱스 파일을 재생성한다."""
        idx_path = get_references_index_path()
        idx_path.parent.mkdir(parents=True, exist_ok=True)

        categories: dict[str, list[str]] = {}
        for cat, card_ids in self._by_category.items():
            categories[cat] = sorted(card_ids)

        # 태그 가중치 계산
        tag_weights: dict[str, int] = {}
        for card in self._cards.values():
            for tag in card.tags:
                tag_weights[tag] = tag_weights.get(tag, 0) + 1

        index_data = {
            "version": "1",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "total_cards": len(self._cards),
            "categories": categories,
            "tag_weights": dict(sorted(tag_weights.items(), key=lambda x: -x[1])),
        }

        with open(idx_path, "w", encoding="utf-8") as f:
            _yaml_dump(index_data, f)

        return idx_path

    @property
    def card_count(self) -> int:
        return len(self._cards)

    @property
    def categories(self) -> list[str]:
        return self.get_all_categories()



class DesignGraph:
    """디자인 지식 그래프 — 컴포넌트/타이포그래피/컬러/모션/스페이싱 등
    모든 디자인 노드를 카테고리별로 인덱싱하여 빠른 검색을 제공한다.

    StyleCard.decomposed_cards에서 추출한 ComponentCard들을 포함해
    Typography, Color, Motion, Spacing, Layout 등 모든 디자인 노드를
    통합 관리하는 그래프 기반 인덱스 시스템.

    (이전 명칭: ComponentRegistry → DesignGraph로 진화)
    """

    def __init__(self):
        self._components: dict[str, "ComponentCard"] = {}
        self._by_category: dict[str, list[str]] = {}
        self._by_sub_category: dict[str, dict[str, list[str]]] = {}

    # ── ingestion ──────────────────────────────────────────────────────

    def ingest_from_registry(self, style_card_registry: "StyleCardRegistry") -> int:
        """StyleCardRegistry의 모든 StyleCard를 분해하여 인덱싱한다."""
        added = 0
        for card in style_card_registry._cards.values():
            components = card.decompose_to_components()
            for comp in components:
                if self.add(comp):
                    added += 1
        return added

    def add(self, component: "ComponentCard") -> bool:
        """ComponentCard를 레지스트리에 추가한다."""
        if not component.id:
            return False
        is_new = component.id not in self._components
        self._components[component.id] = component

        # 카테고리 인덱스 갱신
        cat = component.category or "general"
        if cat not in self._by_category:
            self._by_category[cat] = []
        if component.id not in self._by_category[cat]:
            self._by_category[cat].append(component.id)

        # 서브카테고리 인덱스 갱신
        sub = component.sub_category or "_none"
        if cat not in self._by_sub_category:
            self._by_sub_category[cat] = {}
        if sub not in self._by_sub_category[cat]:
            self._by_sub_category[cat][sub] = []
        if component.id not in self._by_sub_category[cat][sub]:
            self._by_sub_category[cat][sub].append(component.id)

        return is_new

    def remove(self, component_id: str) -> bool:
        """컴포넌트를 레지스트리에서 제거한다."""
        comp = self._components.pop(component_id, None)
        if comp is None:
            return False
        cat = comp.category or "general"
        sub = comp.sub_category or "_none"
        if cat in self._by_category and component_id in self._by_category[cat]:
            self._by_category[cat].remove(component_id)
        if (
            cat in self._by_sub_category
            and sub in self._by_sub_category[cat]
            and component_id in self._by_sub_category[cat][sub]
        ):
            self._by_sub_category[cat][sub].remove(component_id)
        return True

    # ── query ──────────────────────────────────────────────────────────

    def get(self, component_id: str) -> "Optional[ComponentCard]":
        """ID로 단일 컴포넌트를 조회한다."""
        return self._components.get(component_id)

    def get_by_category(self, category: str) -> list["ComponentCard"]:
        """특정 카테고리의 모든 컴포넌트를 반환한다."""
        ids = self._by_category.get(category, [])
        return [self._components[cid] for cid in ids if cid in self._components]

    def get_by_sub_category(self, category: str, sub_category: str) -> list["ComponentCard"]:
        """특정 카테고리+서브카테고리의 컴포넌트를 반환한다."""
        ids = self._by_sub_category.get(category, {}).get(sub_category, [])
        return [self._components[cid] for cid in ids if cid in self._components]

    def get_sub_categories_for(self, category: str) -> list[str]:
        """특정 카테고리의 모든 서브카테고리 목록을 반환한다."""
        return sorted(self._by_sub_category.get(category, {}).keys())

    def get_top_in_category(
        self, category: str, top_n: int = 5, min_score: float = 0.0
    ) -> list["ComponentCard"]:
        """특정 카테고리에서 평가 점수 상위 N개 컴포넌트를 반환한다."""
        comps = self.get_by_category(category)
        scored = [(c, c.evaluate()) for c in comps if c.evaluate() >= min_score]
        scored.sort(key=lambda x: -x[1])
        return [c for c, _ in scored[:top_n]]

    # ── meta ───────────────────────────────────────────────────────────

    def get_all_categories(self) -> list[str]:
        """등록된 모든 카테고리 목록을 반환한다."""
        return sorted(self._by_category.keys())

    def get_all_sub_categories(self, category: str = None) -> dict[str, list[str]]:
        """모든 또는 특정 카테고리의 서브카테고리 맵을 반환한다."""
        if category:
            return {category: self.get_sub_categories_for(category)}
        result: dict[str, list[str]] = {}
        for cat in self._by_sub_category:
            result[cat] = sorted(self._by_sub_category[cat].keys())
        return result

    def get_component_map(self) -> dict[str, int]:
        """카테고리 → 등록된 컴포넌트 수 매핑을 반환한다."""
        return {cat: len(ids) for cat, ids in self._by_category.items()}

    def get_category_summary(self) -> dict:
        """카테고리별 요약 통계를 반환한다."""
        summary: dict[str, dict] = {}
        for cat in self._by_category:
            sub_cats = self._by_sub_category.get(cat, {})
            summary[cat] = {
                "total": len(self._by_category[cat]),
                "sub_categories": len(sub_cats),
                "sub_breakdown": {sc: len(ids) for sc, ids in sub_cats.items()},
            }
        return summary

    def rebuild_from_style_cards(self, style_card_registry: "StyleCardRegistry") -> int:
        """전체 인덱스를 초기화하고 StyleCardRegistry로부터 재구축한다."""
        self._components.clear()
        self._by_category.clear()
        self._by_sub_category.clear()
        return self.ingest_from_registry(style_card_registry)

    # ── properties ─────────────────────────────────────────────────────

    @property
    def component_count(self) -> int:
        return len(self._components)

    @property
    def categories(self) -> list[str]:
        return self.get_all_categories()

    @property
    def sub_categories(self) -> dict[str, list[str]]:
        return self.get_all_sub_categories()


# ── Global singletons ───────────────────────────────────────────────────────

_design_graph: Optional[DesignGraph] = None
_style_card_registry: Optional[StyleCardRegistry] = None


def get_design_graph() -> DesignGraph:
    """DesignGraph 싱글톤을 반환한다."""
    global _design_graph
    if _design_graph is None:
        _design_graph = DesignGraph()
    return _design_graph


def get_component_registry() -> DesignGraph:
    """[하위호환] DesignGraph 싱글톤을 반환한다.
    
    v2.1부터 ComponentRegistry → DesignGraph로 이름이 변경되었다.
    get_design_graph()를 사용하는 것이 권장된다.
    """
    return get_design_graph()


def get_style_card_registry() -> StyleCardRegistry:
    """StyleCardRegistry 싱글톤을 반환한다."""
    global _style_card_registry
    if _style_card_registry is None:
        _style_card_registry = StyleCardRegistry()
    return _style_card_registry

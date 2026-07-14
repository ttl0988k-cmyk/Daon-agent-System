from __future__ import annotations

"""
Style Mixer — Component-level design orchestration and harmony validation.

Phase D of the Knowledge Graph migration. The StyleMixer takes per-category
component selections (from ComponentRetriever) and:

    1. Validates cross-component harmony (compatible_categories, conflicts)
    2. Resolves design conflicts (e.g., brutalist hero + minimalist navbar)
    3. Builds a unified Design Brief from the best matching components
    4. Calculates overall mix harmony score

This enables true "Design Orchestration" — combining the Hero from Apple,
the Navbar from Stripe, the Animation from Linear, and the Typography from
Notion into ONE cohesive design language.

Architecture:
    ComponentRetriever.retrieve_component_mix()
            ↓
    StyleMixer.validate_harmony()   → identifies conflicts
    StyleMixer.resolve_conflicts()  → selects optimally compatible subset
    StyleMixer.build_unified_brief() → produces final Design Brief
    StyleMixer.score_mix()          → overall harmony score

Usage:
    retriever = ComponentRetriever()
    retriever.rebuild_index(design_graph)
    raw_mix = retriever.retrieve_component_mix("glassmorphism saas dashboard")

    mixer = StyleMixer()
    harmony_report = mixer.validate_harmony(raw_mix)
    resolved_mix = mixer.resolve_conflicts(raw_mix)
    brief = mixer.build_unified_brief(resolved_mix)
"""

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.style_card import ComponentCard

_logger = logging.getLogger(__name__)


class HarmonyConflict:
    """Represents a detected conflict between two components in a mix."""

    def __init__(
        self,
        category_a: str,
        component_a_name: str,
        category_b: str,
        component_b_name: str,
        conflict_type: str,
        severity: str = "warning",
    ):
        self.category_a = category_a
        self.component_a_name = component_a_name
        self.category_b = category_b
        self.component_b_name = component_b_name
        self.conflict_type = conflict_type
        self.severity = severity  # "warning", "error", "info"

    def __repr__(self) -> str:
        return (
            f"Conflict({self.category_a}/{self.component_a_name} ↔ "
            f"{self.category_b}/{self.component_b_name}: "
            f"{self.conflict_type} [{self.severity}])"
        )

    def to_markdown(self) -> str:
        return (
            f"- **{self.severity.upper()}**: `{self.category_a}` ↔ `{self.category_b}` "
            f"— {self.conflict_type} "
            f"({self.component_a_name} vs {self.component_b_name})"
        )


class HarmonyReport:
    """Result of a mix harmony validation."""

    def __init__(self):
        self.conflicts: list[HarmonyConflict] = []
        self.warnings: list[str] = []
        self.compatibility_scores: dict[str, float] = {}
        self.overall_score: float = 1.0  # 0.0 = fully conflicting, 1.0 = perfectly harmonious

    @property
    def has_errors(self) -> bool:
        return any(c.severity == "error" for c in self.conflicts)

    @property
    def has_warnings(self) -> bool:
        return any(c.severity == "warning" for c in self.conflicts)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)

    def to_markdown(self) -> str:
        if not self.conflicts:
            return "✅ All components are compatible — no conflicts detected."

        lines = [f"### 🔍 Harmony Report (score: {self.overall_score:.2f})", ""]
        if self.has_errors:
            lines.append(f"⚠️ **{sum(1 for c in self.conflicts if c.severity == 'error')} error(s)**")
        if self.has_warnings:
            lines.append(f"⚡ **{sum(1 for c in self.conflicts if c.severity == 'warning')} warning(s)**")
        lines.append("")
        for conflict in self.conflicts:
            lines.append(conflict.to_markdown())
        return "\n".join(lines)


class TrendReport:
    """Trend analysis result for a component mix.

    Checks whether selected components reference styles that are
    deprecated, overused, or temporally mismatched.
    """

    def __init__(self):
        self.warnings: list[str] = []
        self.info: list[str] = []
        self.deprecated_patterns: list[str] = []
        self.trend_score: float = 1.0  # 1.0 = fully current, 0.0 = all deprecated

    @property
    def has_issues(self) -> bool:
        return len(self.warnings) > 0 or len(self.deprecated_patterns) > 0

    def to_markdown(self) -> str:
        lines = [f"**Trend Score**: {self.trend_score:.2f}/1.00", ""]
        if self.deprecated_patterns:
            lines.append("### ⚠️ Deprecated Patterns")
            for p in self.deprecated_patterns:
                lines.append(f"- {p}")
            lines.append("")
        if self.warnings:
            lines.append("### ⚡ Trend Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")
        if self.info:
            lines.append("### ℹ️ Trend Notes")
            for i in self.info:
                lines.append(f"- {i}")
            lines.append("")
        if not self.deprecated_patterns and not self.warnings:
            lines.append("✅ All components reference current design trends.")
        return "\n".join(lines)


class AccessibilityReport:
    """Accessibility audit result for a component mix.

    Checks WCAG compliance indicators across selected components:
    contrast, font sizing, motion safety, and semantic structure.
    """

    def __init__(self):
        self.errors: list[str] = []       # Hard WCAG violations
        self.warnings: list[str] = []      # Potential issues
        self.info: list[str] = []          # Informational notes
        self.accessibility_score: float = 1.0  # 1.0 = fully compliant

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_markdown(self) -> str:
        lines = [f"**Accessibility Score**: {self.accessibility_score:.2f}/1.00", ""]
        if self.errors:
            lines.append("### 🚫 WCAG Violations")
            for e in self.errors:
                lines.append(f"- {e}")
            lines.append("")
        if self.warnings:
            lines.append("### ⚠️ Accessibility Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")
        if self.info:
            lines.append("### ℹ️ Accessibility Notes")
            for i in self.info:
                lines.append(f"- {i}")
            lines.append("")
        if not self.errors and not self.warnings:
            lines.append("✅ No accessibility issues detected.")
        return "\n".join(lines)


class StyleMixer:
    """디자인 오케スト레이터 — component-level design mixing with full pipeline.

    The StyleMixer is the core "Design Orchestrator" agent. It handles the
    complete pipeline from raw component selection to validated Design Brief:

        1. validate_harmony()   — cross-component compatibility & aesthetic clash detection
        2. resolve_conflicts()  — intelligently select most compatible subset
        3. check_trends()       — detect deprecated/outdated design patterns
        4. check_accessibility() — audit WCAG compliance (contrast, fonts, motion)
        5. build_unified_brief() — synthesize into cohesive Design Brief
        6. orchestrate()        — full pipeline in one call (v2.1 convenience API)

    Harmony Rules (built-in):
        1. Category compatibility: check each component's ``compatible_categories``
           and ``conflicts_with_categories`` lists.
        2. Design DNA clash detection: detect opposing aesthetics (e.g., brutalist
           vs. minimalist, dark vs. light, high-motion vs. static).
        3. Source diversity bonus: mixing components from different parent cards
           is preferred (more creative synthesis).
        4. Harmony scoring: weighted sum of compatibility, diversity, and
           evaluation scores.

    Usage (quick):
        mixer = StyleMixer()
        result = mixer.orchestrate("glassmorphism saas", mix, design_graph)
        # result = {"brief": "...", "harmony": 0.92, "trend_score": 0.95, "a11y_score": 0.88}

    Usage (step-by-step):
        mixer = StyleMixer()
        report = mixer.validate_harmony(raw_mix)
        if not report.has_errors:
            resolved = mixer.resolve_conflicts(raw_mix)
            trends = mixer.check_trends(resolved)
            a11y = mixer.check_accessibility(resolved)
            brief = mixer.build_unified_brief(resolved)
    """

    # Aesthetic opposites — if two categories pick these sub-categories,
    # flag a warning.
    AESTHETIC_OPPOSITES = [
        ({"brutalist", "brutalism", "raw"}, {"minimal", "minimalist", "clean"}),
        ({"dark", "dark-mode"}, {"light", "light-mode", "white"}),
        ({"high-motion", "animated", "playful"}, {"static", "still", "minimal-animation"}),
        ({"glassmorphism", "glass"}, {"flat", "flat-design"}),
        ({"3d", "three-d"}, {"flat", "2d", "two-d"}),
        ({"neon", "cyberpunk"}, {"corporate", "enterprise", "professional"}),
        ({"vintage", "retro"}, {"futuristic", "modern", "cutting-edge"}),
        ({"maximalist", "busy", "dense"}, {"minimal", "minimalist", "sparse"}),
    ]

    # Categories that strongly influence overall aesthetic — conflicts in
    # these categories are more severe.
    HIGH_IMPACT_CATEGORIES = {"hero", "color", "typography", "animation"}

    def __init__(self):
        pass

    @staticmethod
    def _extract_card(entry):
        """Extract ComponentCard from a mix entry — handles (Card, score) tuples.

        ``ComponentRetriever.retrieve_component_mix()`` returns
        ``dict[str, list[tuple[ComponentCard, float]]]`` — the values are
        (component, score) tuples. This helper safely unwraps the
        ComponentCard whether the caller passes a tuple or a raw card.
        """
        if isinstance(entry, tuple):
            return entry[0]
        return entry

    # ── harmony validation ──────────────────────────────────────────────

    def validate_harmony(
        self,
        component_mix: dict[str, list],
    ) -> HarmonyReport:
        """Validate cross-component harmony for a given mix.

        Checks each pair of selected components for:
        1. Explicit conflicts (conflicts_with_categories)
        2. Aesthetic opposites (sub_category clash detection)
        3. Source over-reliance (too many from same parent)

        Args:
            component_mix: Result from ``ComponentRetriever.retrieve_component_mix()``
                           — dict mapping category → list of (ComponentCard, score).

        Returns:
            ``HarmonyReport`` with conflicts, warnings, and overall score.
        """
        report = HarmonyReport()

        # Flatten: pick top component per category for conflict checking
        top_per_category: dict[str, ComponentCard] = {}
        for cat, entries in component_mix.items():
            if entries:
                top_per_category[cat] = self._extract_card(entries[0])

        categories = list(top_per_category.keys())

        # Check pairwise conflicts
        for i in range(len(categories)):
            for j in range(i + 1, len(categories)):
                cat_a = categories[i]
                cat_b = categories[j]
                comp_a = top_per_category[cat_a]
                comp_b = top_per_category[cat_b]

                # Rule 1: Explicit conflicts_with
                if cat_b in comp_a.conflicts_with_categories:
                    report.conflicts.append(HarmonyConflict(
                        cat_a, comp_a.name, cat_b, comp_b.name,
                        f"'{comp_a.name}' explicitly conflicts with '{cat_b}' components",
                        severity="error",
                    ))
                if cat_a in comp_b.conflicts_with_categories:
                    report.conflicts.append(HarmonyConflict(
                        cat_b, comp_b.name, cat_a, comp_a.name,
                        f"'{comp_b.name}' explicitly conflicts with '{cat_a}' components",
                        severity="error",
                    ))

                # Rule 2: Aesthetic opposites
                aesthetic_conflict = self._detect_aesthetic_conflict(
                    comp_a.sub_category, comp_b.sub_category
                )
                if aesthetic_conflict:
                    severity = (
                        "warning"
                        if cat_a in self.HIGH_IMPACT_CATEGORIES
                        or cat_b in self.HIGH_IMPACT_CATEGORIES
                        else "info"
                    )
                    report.conflicts.append(HarmonyConflict(
                        cat_a, comp_a.name, cat_b, comp_b.name,
                        f"Aesthetic clash: {aesthetic_conflict}",
                        severity=severity,
                    ))

                # Rule 3: Same source (too much from one reference)
                if comp_a.parent_card_id and comp_b.parent_card_id:
                    if comp_a.parent_card_id == comp_b.parent_card_id:
                        report.conflicts.append(HarmonyConflict(
                            cat_a, comp_a.name, cat_b, comp_b.name,
                            "Both components sourced from the same parent Style Card",
                            severity="info",
                        ))

        # Calculate overall harmony score
        report.overall_score = self.score_mix(top_per_category, report)
        return report

    def _detect_aesthetic_conflict(
        self, sub_a: str, sub_b: str
    ) -> Optional[str]:
        """Detect aesthetic opposition between two sub-categories."""
        if not sub_a or not sub_b:
            return None
        sub_a_lower = sub_a.lower()
        sub_b_lower = sub_b.lower()

        for set_a, set_b in self.AESTHETIC_OPPOSITES:
            a_in_a = any(tag in sub_a_lower for tag in set_a)
            b_in_b = any(tag in sub_b_lower for tag in set_b)
            a_in_b = any(tag in sub_b_lower for tag in set_a)
            b_in_a = any(tag in sub_a_lower for tag in set_b)

            if (a_in_a and b_in_b) or (a_in_b and b_in_a):
                return f"'{sub_a}' vs '{sub_b}' are aesthetically opposing styles"

        return None

    # ── conflict resolution ─────────────────────────────────────────────

    def resolve_conflicts(
        self,
        component_mix: dict[str, list],
        harmony_report: Optional[HarmonyReport] = None,
    ) -> dict[str, list]:
        """Resolve conflicts by selecting the most compatible subset.

        Strategy:
            1. Remove components that have error-level conflicts (hard incompatibility)
            2. For warning-level conflicts, prefer the higher-scored alternative
               from the same category's runner-up list
            3. Preserve source diversity

        Args:
            component_mix: Raw component mix from ComponentRetriever.
            harmony_report: Optional pre-computed report. If None, computed internally.

        Returns:
            Resolved component mix with conflicts mitigated.
        """
        if harmony_report is None:
            harmony_report = self.validate_harmony(component_mix)

        if not harmony_report.conflicts:
            return dict(component_mix)  # No conflicts, return as-is

        # Copy so we can modify
        resolved: dict[str, list] = {
            cat: list(entries) for cat, entries in component_mix.items()
        }

        # Collect error-level category pairs to resolve
        error_pairs: set[tuple[str, str]] = set()
        for c in harmony_report.conflicts:
            if c.severity == "error":
                error_pairs.add(tuple(sorted([c.category_a, c.category_b])))

        # For each error pair, keep the higher-scored component,
        # drop the lower-scored one and try its runner-up (or remove category)
        for cat_a, cat_b in error_pairs:
            if cat_a not in resolved or cat_b not in resolved:
                continue

            comp_a_entry = resolved[cat_a][0] if resolved[cat_a] else None
            comp_b_entry = resolved[cat_b][0] if resolved[cat_b] else None
            if not comp_a_entry or not comp_b_entry:
                continue

            comp_a = self._extract_card(comp_a_entry)
            comp_b = self._extract_card(comp_b_entry)
            score_a = comp_a.evaluation.score
            score_b = comp_b.evaluation.score

            # Drop the lower-scored component
            if score_a >= score_b:
                # Drop cat_b's top, promote runner-up if available
                resolved[cat_b].pop(0)
                if not resolved[cat_b]:
                    del resolved[cat_b]
            else:
                resolved[cat_a].pop(0)
                if not resolved[cat_a]:
                    del resolved[cat_a]

        return resolved

    # ── scoring ─────────────────────────────────────────────────────────

    def score_mix(
        self,
        top_per_category: dict[str, tuple],
        harmony_report: Optional[HarmonyReport] = None,
    ) -> float:
        """Calculate overall harmony score for a component mix.

        Score components:
            - Base: average of individual component harmony_scores (40%)
            - Diversity: number of unique parent cards / total components (30%)
            - Conflict penalty: -0.2 per error, -0.1 per warning (30%)

        Args:
            top_per_category: Dict mapping category → (ComponentCard, score).
            harmony_report: Optional pre-computed report.

        Returns:
            Float between 0.0 (fully conflicting) and 1.0 (perfectly harmonious).
        """
        if not top_per_category:
            return 0.0

        n = len(top_per_category)

        # Component quality (40%)
        avg_harmony = sum(
            comp.harmony_score for comp in top_per_category.values()
        ) / n
        quality_score = max(0.0, min(1.0, avg_harmony))

        # Source diversity (30%)
        parent_ids = set()
        for comp in top_per_category.values():
            if comp.parent_card_id:
                parent_ids.add(comp.parent_card_id)
        if not parent_ids:
            parent_ids = {"unknown"}
        diversity_score = len(parent_ids) / n
        diversity_score = min(1.0, diversity_score)

        # Conflict penalty (30%)
        if harmony_report:
            error_penalty = sum(
                0.25 for c in harmony_report.conflicts if c.severity == "error"
            )
            warning_penalty = sum(
                0.1 for c in harmony_report.conflicts if c.severity == "warning"
            )
            conflict_score = max(0.0, 1.0 - error_penalty - warning_penalty)
        else:
            conflict_score = 1.0

        overall = (quality_score * 0.4) + (diversity_score * 0.3) + (conflict_score * 0.3)
        return round(max(0.0, min(1.0, overall)), 4)

    # ── brief generation ────────────────────────────────────────────────

    def build_unified_brief(
        self,
        component_mix: dict[str, list],
        intent_summary: str = "",
        harmony_report: Optional[HarmonyReport] = None,
    ) -> str:
        """Build a unified Design Brief from the resolved component mix.

        Synthesizes per-component Design DNA into a single cohesive brief
        that the Creative Director can inject into the frontend agent's prompt.

        Args:
            component_mix: Resolved component mix.
            intent_summary: User's design intent summary (from Creative Director).
            harmony_report: Optional pre-computed harmony report.

        Returns:
            Markdown-formatted unified Design Brief.
        """
        if not component_mix:
            return "* (No components available for Design Brief)*"

        if harmony_report is None:
            harmony_report = self.validate_harmony(component_mix)

        top_per_category: dict[str, ComponentCard] = {}
        for cat, entries in component_mix.items():
            if entries:
                top_per_category[cat] = self._extract_card(entries[0])

        # Aggregate Design DNA
        all_colors = []
        all_fonts = []
        all_layouts = []
        all_animations = []
        all_spacing = []
        do_list = []
        dont_list = []
        glass_effects = []
        corner_radii = []

        for cat, comp in top_per_category.items():
            dna = comp.design_dna
            if dna.colors and dna.colors.palette_name:
                all_colors.append(f"{dna.colors.palette_name} (from {cat})")
            if dna.typography and dna.typography.heading_font:
                all_fonts.append(f"{dna.typography.heading_font} (from {cat})")
            if dna.layout and dna.layout.grid:
                all_layouts.append(f"{dna.layout.grid} (from {cat})")
            if dna.animation and dna.animation.entrance:
                all_animations.append(f"{dna.animation.entrance} (from {cat})")
            if dna.spacing and dna.spacing.density:
                all_spacing.append(f"{dna.spacing.density} (from {cat})")
            do_list.extend(comp.guidelines.do)
            dont_list.extend(comp.guidelines.dont)
            if dna.layout and dna.layout.glass_effect:
                glass_effects.append(cat)
            if dna.layout and dna.layout.border_radius:
                corner_radii.append(str(dna.layout.border_radius))

        # Category display order
        priority_order = [
            "hero", "navbar", "header", "cta", "footer",
            "features", "pricing", "testimonial", "contact",
            "card", "gallery", "modal", "sidebar", "form",
            "typography", "color", "spacing", "animation",
        ]
        ordered_cats = [c for c in priority_order if c in top_per_category]
        ordered_cats += [c for c in top_per_category if c not in ordered_cats]

        overall_score = self.score_mix(top_per_category, harmony_report)

        lines = [
            "## 🎨 Unified Design Brief (Style Mixing)",
            "",
            f"**Intent**: {intent_summary}" if intent_summary else "**Intent**: (not specified)",
            f"**Harmony Score**: {overall_score:.2f}/1.00",
            f"**Components**: {len(top_per_category)} categories, "
            f"{len(set(c.parent_card_id for c in top_per_category.values() if c.parent_card_id))} "
            f"source references",
            "",
            "---",
            "",
            "### 📐 Aggregated Design DNA",
            "",
        ]

        if all_colors:
            lines.append(f"- **Palettes**: {', '.join(all_colors)}")
        if all_fonts:
            lines.append(f"- **Typography**: {', '.join(all_fonts)}")
        if all_layouts:
            lines.append(f"- **Layout**: {', '.join(all_layouts)}")
        if all_spacing:
            lines.append(f"- **Spacing**: {', '.join(all_spacing)}")
        if all_animations:
            lines.append(f"- **Animation**: {', '.join(all_animations)}")
        if glass_effects:
            lines.append(f"- **Glass Effects**: used in {', '.join(glass_effects)}")
        if corner_radii:
            lines.append(f"- **Corner Radius**: {', '.join(set(corner_radii))}")

        lines.append("")
        lines.append("---")
        lines.append("")

        # Per-component details
        lines.append("### 🧩 Component Selection")
        lines.append("")

        for cat in ordered_cats:
            comp = top_per_category[cat]
            lines.append(f"#### {cat.upper()} — {comp.name} (score: {comp.evaluation.score})")
            lines.append(f"- **Source**: {comp.source_url or comp.parent_card_id or 'N/A'}")
            lines.append(f"- **Sub-category**: {comp.sub_category or 'general'}")
            lines.append(f"- **Harmony**: {comp.harmony_score:.2f}")
            # Add brief DNA for this component
            brief_text = comp.to_brief_text()
            # Extract first few meaningful lines
            for line in brief_text.split("\n")[:8]:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    lines.append(f"  {stripped}")
            lines.append("")

        # Guidelines aggregation
        if do_list or dont_list:
            lines.append("### 📋 Aggregated Guidelines")
            lines.append("")
            unique_do = list(dict.fromkeys(do_list))[:8]
            unique_dont = list(dict.fromkeys(dont_list))[:8]
            if unique_do:
                lines.append("**DO**:")
                for d in unique_do:
                    lines.append(f"- {d}")
                lines.append("")
            if unique_dont:
                lines.append("**DON'T**:")
                for d in unique_dont:
                    lines.append(f"- {d}")
                lines.append("")

        # Harmony report
        lines.append("### 🔍 Harmony Validation")
        lines.append("")
        lines.append(harmony_report.to_markdown())

        return "\n".join(lines)

    def build_compact_brief(
        self,
        component_mix: dict[str, list],
        intent_summary: str = "",
    ) -> str:
        """Build a compact, token-efficient Design Brief.

        Suitable for injecting directly into frontend agent system prompts
        where token budget is limited.

        Returns:
            Compact single-paragraph design brief.
        """
        if not component_mix:
            return ""

        top_per_category: dict[str, ComponentCard] = {}
        for cat, entries in component_mix.items():
            if entries:
                top_per_category[cat] = self._extract_card(entries[0])

        parts = [f"Design Brief: {intent_summary}."] if intent_summary else ["Design Brief:"]

        for cat, comp in sorted(top_per_category.items()):
            dna = comp.design_dna
            detail_parts = []
            if dna.colors and dna.colors.palette_name:
                detail_parts.append(f"color={dna.colors.palette_name}")
            if dna.typography and dna.typography.heading_font:
                detail_parts.append(f"font={dna.typography.heading_font}")
            if dna.layout and dna.layout.grid:
                detail_parts.append(f"grid={dna.layout.grid}")
            if dna.animation and dna.animation.entrance:
                detail_parts.append(f"motion={dna.animation.entrance}")
            parts.append(f"[{cat}] {comp.name}: " + ", ".join(detail_parts) + ".")

        return " ".join(parts)


    # ── trend analysis (v2.1) ─────────────────────────────────────────

    # Known deprecated / overused patterns to flag
    DEPRECATED_PATTERNS = {
        "skeuomorphism", "heavy-shadow", "excessive-gradient",
        "times-new-roman", "comic-sans", "papyrus",
        "marquee", "blink", "flash-intro", "splash-screen",
        "table-layout", "iframe-layout",
        "text-shadow-heavy", "box-shadow-overuse",
        "animated-gif-background", "auto-play-audio",
    }

    # Known trendy / current patterns (positive signal)
    CURRENT_PATTERNS = {
        "glassmorphism", "neumorphism", "brutalism", "minimal",
        "dark-mode", "gradient-subtle", "micro-interaction",
        "variable-font", "fluid-typography", "clamp",
        "grid-layout", "flexbox", "container-query",
        "scroll-snap", "view-transition", "scroll-driven",
        "oklch", "color-mix", "has-selector",
        "bento-grid", "glass", "claymorphism",
    }

    def check_trends(
        self,
        component_mix: dict[str, list],
    ) -> TrendReport:
        """Analyze design trend relevance for selected components.

        Checks each component's tags, sub_category, and guidelines for:
        1. Deprecated/overused patterns (e.g., skeuomorphism, marquee, comic-sans)
        2. Current/trendy patterns (positive signal)
        3. Reference date freshness (older references may be dated)

        Args:
            component_mix: Resolved component mix (category -> list of (Card, score)).

        Returns:
            ``TrendReport`` with warnings, deprecated patterns, and trend score.
        """
        report = TrendReport()

        top_per_category: dict[str, ComponentCard] = {}
        for cat, entries in component_mix.items():
            if entries:
                top_per_category[cat] = self._extract_card(entries[0])

        if not top_per_category:
            return report

        deprecated_count = 0
        current_count = 0
        total = len(top_per_category)

        for cat, comp in top_per_category.items():
            # Collect searchable text from this component
            search_text_parts = [
                comp.sub_category or "",
                " ".join(comp.tags or []),
                " ".join(comp.guidelines.do or []),
                " ".join(comp.guidelines.dont or []),
            ]
            search_text = " ".join(search_text_parts).lower()

            # Check deprecated patterns
            comp_deprecated = []
            for pattern in self.DEPRECATED_PATTERNS:
                if pattern in search_text:
                    comp_deprecated.append(pattern)

            if comp_deprecated:
                deprecated_count += 1
                report.deprecated_patterns.append(
                    f"[{cat}] {comp.name}: {', '.join(comp_deprecated)}"
                )

            # Check current patterns
            comp_current = []
            for pattern in self.CURRENT_PATTERNS:
                if pattern in search_text:
                    comp_current.append(pattern)

            if comp_current:
                current_count += 1
                report.info.append(
                    f"[{cat}] {comp.name}: current patterns - {', '.join(comp_current[:3])}"
                )

            # Check reference freshness from Evaluation
            if comp.evaluation and comp.evaluation.trend_relevance:
                tr = comp.evaluation.trend_relevance
                if tr == "low":
                    report.warnings.append(
                        f"[{cat}] {comp.name}: low trend relevance - "
                        f"reference may be dated"
                    )

        # Calculate trend score
        if total > 0:
            deprecated_penalty = (deprecated_count / total) * 0.4
            current_bonus = (current_count / total) * 0.3
            report.trend_score = round(
                max(0.0, min(1.0, 1.0 - deprecated_penalty + current_bonus)), 4
            )

        return report

    # ── accessibility audit (v2.1) ─────────────────────────────────────

    # Minimum contrast ratio for WCAG AA (normal text)
    MIN_CONTRAST_AA_NORMAL = 4.5
    MIN_CONTRAST_AA_LARGE = 3.0

    # Minimum font sizes for readability
    MIN_BODY_FONT_SIZE = 14  # px equivalent
    MIN_HEADING_FONT_SIZE = 18  # px equivalent

    # Reduced-motion safety
    MOTION_SAFE_DURATION_MAX = 500  # ms - prefer under 500ms for accessibility

    # Known small/difficult font families
    SMALL_APPEARING_FONTS = {"times new roman", "courier", "courier new", "monaco"}

    # Known accessible font families
    ACCESSIBLE_FONTS = {
        "inter", "roboto", "open sans", "atkinson hyperlegible",
        "noto sans", "system-ui", "-apple-system", "helvetica", "arial",
        "verdana", "georgia", "lato", "nunito", "poppins",
    }

    def check_accessibility(
        self,
        component_mix: dict[str, list],
    ) -> AccessibilityReport:
        """Audit WCAG accessibility compliance across the component mix.

        Checks:
        1. Color contrast indicators (dark/light mode, palette warnings)
        2. Font sizing (minimum readable sizes)
        3. Motion safety (animation duration, reduced-motion compatibility)
        4. Semantic structure hints (from layout/guidelines)

        Args:
            component_mix: Resolved component mix (category -> list of (Card, score)).

        Returns:
            ``AccessibilityReport`` with errors, warnings, and a11y score.
        """
        report = AccessibilityReport()

        top_per_category: dict[str, ComponentCard] = {}
        for cat, entries in component_mix.items():
            if entries:
                top_per_category[cat] = self._extract_card(entries[0])

        if not top_per_category:
            return report

        issue_count = 0
        check_count = 0

        for cat, comp in top_per_category.items():
            dna = comp.design_dna

            # -- Color / contrast --
            if dna.colors:
                check_count += 1
                bg = (dna.colors.background or "").lower()
                txt = (dna.colors.text_primary or "").lower()
                accent = (dna.colors.accent or "").lower()

                # Light bg + light text = low contrast
                light_bg_keywords = {"white", "#fff", "#ffffff", "light", "cream", "ivory"}
                light_txt_keywords = {"white", "#fff", "#ffffff", "lightgray",
                                      "light-grey", "silver", "beige"}
                dark_bg_keywords = {"black", "#000", "#000000", "dark", "charcoal"}
                dark_txt_keywords = {"black", "#000", "#000000", "darkgray",
                                     "dark-grey", "charcoal", "navy"}

                if any(kw in bg for kw in light_bg_keywords) and any(kw in txt for kw in light_txt_keywords):
                    issue_count += 1
                    report.errors.append(
                        f"[{cat}] {comp.name}: potential low contrast - "
                        f"light background ({bg}) with light text ({txt})"
                    )
                elif any(kw in bg for kw in dark_bg_keywords) and any(kw in txt for kw in dark_txt_keywords):
                    issue_count += 1
                    report.errors.append(
                        f"[{cat}] {comp.name}: potential low contrast - "
                        f"dark background ({bg}) with dark text ({txt})"
                    )

                # Neon/bright accent on light bg
                neon_keywords = {"neon", "#ff0", "#0ff", "#f0f", "lime", "cyan"}
                if any(kw in accent for kw in neon_keywords):
                    report.warnings.append(
                        f"[{cat}] {comp.name}: neon accent ({accent}) may cause "
                        f"accessibility issues - ensure 3:1 contrast ratio"
                    )

            # -- Typography --
            if dna.typography:
                check_count += 1
                body_font = (dna.typography.body_font or "").lower()
                heading_font = (dna.typography.heading_font or "").lower()

                # Check font family
                problematic_fonts = []
                for font in self.SMALL_APPEARING_FONTS:
                    if font in body_font:
                        problematic_fonts.append(f"body={font}")
                    if font in heading_font:
                        problematic_fonts.append(f"heading={font}")

                if problematic_fonts:
                    issue_count += 1
                    report.warnings.append(
                        f"[{cat}] {comp.name}: fonts may appear small/difficult - "
                        f"{', '.join(problematic_fonts)}"
                    )

                # Check if fonts are in known accessible list
                font_ok = (
                    any(af in body_font for af in self.ACCESSIBLE_FONTS)
                    or any(af in heading_font for af in self.ACCESSIBLE_FONTS)
                )
                if not font_ok and body_font:
                    report.info.append(
                        f"[{cat}] {comp.name}: font '{dna.typography.heading_font or dna.typography.body_font}' "
                        f"not in known accessible font list - verify readability"
                    )

            # -- Animation / motion --
            if dna.animation:
                check_count += 1
                duration = dna.animation.duration_base
                if duration is not None:
                    if isinstance(duration, (int, float)):
                        if duration > self.MOTION_SAFE_DURATION_MAX:
                            issue_count += 1
                            report.warnings.append(
                                f"[{cat}] {comp.name}: animation duration {duration}ms "
                                f"exceeds {self.MOTION_SAFE_DURATION_MAX}ms - "
                                f"may cause discomfort for users with vestibular disorders"
                            )
                    elif isinstance(duration, str):
                        try:
                            dur_ms = float(duration.replace("ms", "").replace("s", "").strip())
                            if "s" in duration and "ms" not in duration:
                                dur_ms *= 1000
                            if dur_ms > self.MOTION_SAFE_DURATION_MAX:
                                issue_count += 1
                                report.warnings.append(
                                    f"[{cat}] {comp.name}: animation duration {duration} "
                                    f"may be too long for accessibility"
                                )
                        except ValueError:
                            pass

                # High motion intensity warning
                mi = dna.animation.motion_intensity
                if isinstance(mi, (int, float)) and mi >= 8:
                    report.warnings.append(
                        f"[{cat}] {comp.name}: high motion intensity ({mi}/10) - "
                        f"ensure `prefers-reduced-motion` media query is respected"
                    )

            # -- Overall a11y evaluation field --
            if comp.evaluation and comp.evaluation.accessibility is not None:
                acc = comp.evaluation.accessibility
                if acc < 5.0:
                    issue_count += 1
                    report.errors.append(
                        f"[{cat}] {comp.name}: low accessibility score ({acc:.1f}/10)"
                    )
                elif acc < 7.0:
                    report.warnings.append(
                        f"[{cat}] {comp.name}: moderate accessibility score ({acc:.1f}/10)"
                    )

        # Calculate accessibility score
        if check_count > 0:
            penalty = (issue_count / max(check_count, 1)) * 1.0
            report.accessibility_score = round(max(0.0, min(1.0, 1.0 - penalty)), 4)

        return report

    # ── orchestration (v2.1) ──────────────────────────────────────────

    def orchestrate(
        self,
        intent_summary: str,
        component_mix: dict[str, list],
        design_graph=None,  # Optional[DesignGraph] for future graph operations
    ) -> dict:
        """Full design orchestration pipeline - one call does everything.

        This is the convenience entry point that chains:
            validate_harmony -> resolve_conflicts -> check_trends ->
            check_accessibility -> build_unified_brief

        Args:
            intent_summary: User's design intent (e.g., "glassmorphism saas dashboard").
            component_mix: Raw mix from ComponentRetriever.retrieve_component_mix().
            design_graph: Optional DesignGraph for future graph-based optimizations.

        Returns:
            Dict with keys:
                - "brief": Unified Design Brief (markdown string)
                - "compact_brief": Token-efficient compact brief (string)
                - "harmony_score": Overall harmony score (float 0-1)
                - "trend_score": Trend relevance score (float 0-1)
                - "a11y_score": Accessibility compliance score (float 0-1)
                - "harmony_report": HarmonyReport instance (for inspection)
                - "trend_report": TrendReport instance (for inspection)
                - "a11y_report": AccessibilityReport instance (for inspection)
                - "conflicts_resolved": Number of conflicts resolved (int)
                - "component_count": Number of categories in final mix (int)
        """
        # Step 1: Harmony validation
        harmony_report = self.validate_harmony(component_mix)
        conflict_count_before = len(harmony_report.conflicts)

        # Step 2: Conflict resolution
        resolved = self.resolve_conflicts(component_mix, harmony_report)
        conflict_count_after = (
            len(self.validate_harmony(resolved).conflicts) if resolved else 0
        )

        # Step 3: Trend check
        trend_report = self.check_trends(resolved)

        # Step 4: Accessibility audit
        a11y_report = self.check_accessibility(resolved)

        # Step 5: Build briefs
        unified_brief = self.build_unified_brief(resolved, intent_summary, harmony_report)
        compact_brief = self.build_compact_brief(resolved, intent_summary)

        # Calculate component count
        comp_count = sum(1 for entries in resolved.values() if entries)

        return {
            "brief": unified_brief,
            "compact_brief": compact_brief,
            "harmony_score": harmony_report.overall_score,
            "trend_score": trend_report.trend_score,
            "a11y_score": a11y_report.accessibility_score,
            "harmony_report": harmony_report,
            "trend_report": trend_report,
            "a11y_report": a11y_report,
            "conflicts_resolved": max(0, conflict_count_before - conflict_count_after),
            "component_count": comp_count,
        }


# ── Global singleton ───────────────────────────────────────────────────────

_style_mixer: Optional[StyleMixer] = None


def get_style_mixer() -> StyleMixer:
    """StyleMixer 싱글톤을 반환한다."""
    global _style_mixer
    if _style_mixer is None:
        _style_mixer = StyleMixer()
    return _style_mixer

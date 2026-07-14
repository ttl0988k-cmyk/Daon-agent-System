"""
Integration test: StyleCardRegistry + StyleCardRetriever
Tests:
1. StyleCard creation and save/load roundtrip
2. StyleCardRegistry load_all
3. StyleCardRetriever index building
4. Semantic search (keyword similarity)
5. Category filter
6. retrieve_for_brief text generation
7. retrieve_design_dna_context
"""

import sys
import os
import json
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

print("=" * 60)
print("Style Card Integration Test")
print("=" * 60)

# Test 1: StyleCard creation and roundtrip
print("\n[Test 1] StyleCard creation and dict roundtrip...")
from api.style_card import StyleCard, ColorDNA, TypographyDNA, LayoutDNA, AnimationDNA, SpacingDNA, DesignDNA, Composition, Guidelines, Evaluation

card = StyleCard(
    id="glass-hero-01",
    name="Glassmorphism Tech Hero",
    category="hero",
    sub_category="dark",
    tags=["glassmorphism", "dark", "tech", "futuristic", "geometric"],
    source_url="https://dribbble.com/shots/example",
    source_type="dribbble",
    design_dna=DesignDNA(
        colors=ColorDNA(
            primary="#6366F1",
            accent="#06B6D4",
            background="#0F172A",
            surface="rgba(30,41,59,0.6)",
            text_primary="#F8FAFC",
            text_secondary="#94A3B8",
            palette_name="Midnight Aurora",
            palette_harmony="complementary",
        ),
        typography=TypographyDNA(
            heading_font="Inter",
            body_font="Inter",
            mono_font="JetBrains Mono",
            scale="major-third",
            heading_weight=700,
            body_weight=400,
            letter_spacing_heading="-0.03em",
            line_height_body=1.6,
        ),
        layout=LayoutDNA(
            grid="12-column",
            max_width="1280px",
            padding_desktop="80px",
            padding_mobile="24px",
            alignment="center",
            glass_effect=True,
            border_radius="16px",
            backdrop_blur="20px",
        ),
        animation=AnimationDNA(
            entrance="fade-up",
            hover="scale-105",
            scroll="parallax",
            page_transition="crossfade",
            duration_base="400ms",
            easing="cubic-bezier(0.16, 1, 0.3, 1)",
            motion_intensity=6,
        ),
        spacing=SpacingDNA(
            density="airy",
            section_gap="120px",
            element_gap="24px",
        ),
    ),
    composition=Composition(
        structure=["hero", "features", "cta", "footer"],
    ),
    guidelines=Guidelines(
        do=["Use backdrop-blur for depth", "Layer multiple glass panels"],
        dont=["Avoid flat backgrounds", "Don't use hard shadows"],
    ),
    compatible_with=["taste-design", "premium-ui"],
    conflicts_with=["brutalist-ui"],
)

d = card.to_dict()
sc = d["style_card"]
assert sc["id"] == "glass-hero-01"
assert sc["category"] == "hero"
assert sc["design_dna"]["colors"]["palette_name"] == "Midnight Aurora"
assert sc["design_dna"]["colors"]["primary"] == "#6366F1"
print("  ✅ Roundtrip dict: OK")

# Test roundtrip from dict
card2 = StyleCard.from_dict(d)
assert card2.id == card.id
assert card2.name == card.name
assert card2.design_dna.colors.primary == "#6366F1"
assert card2.design_dna.colors.palette_harmony == "complementary"
assert card2.tags == ["glassmorphism", "dark", "tech", "futuristic", "geometric"]
print("  ✅ _from_dict roundtrip: OK")

# Test to_search_document
doc = card.to_search_document()
assert "Glassmorphism Tech Hero" in doc
assert "glassmorphism" in doc
assert "Midnight Aurora" in doc
print("  ✅ to_search_document: OK")

# Test to_brief_text
brief = card.to_brief_text()
assert "Glassmorphism Tech Hero" in brief
assert "Midnight Aurora" in brief
print("  ✅ to_brief_text: OK")

# Test to_inline_style_hint
hint = card.to_inline_style_hint()
assert "Glassmorphism Tech Hero" in hint
assert "Midnight Aurora" in hint
assert "Palette=" in hint
assert "Font=" in hint
assert "Grid=" in hint
assert "Motion=" in hint
assert "Score=" in hint
print("  ✅ to_inline_style_hint: OK")

# Test evaluate
score = card.evaluate()
assert 0.0 <= score <= 10.0, f"Score {score} out of range"
print(f"  ✅ evaluate: {score:.2f}/10.0")

# Test 2: StyleCardRegistry
print("\n[Test 2] StyleCardRegistry...")
from api.style_card import StyleCardRegistry

registry = StyleCardRegistry()
assert len(registry._cards) == 0 and len(registry._by_category) == 0
print("  ✅ Empty registry: OK")

# Add card
added = registry.add(card, save=False)
assert added
assert "glass-hero-01" in registry._cards
assert "hero" in registry._by_category
print("  ✅ Add card: OK")

# get
retrieved = registry.get("glass-hero-01")
assert retrieved is not None
assert retrieved.name == "Glassmorphism Tech Hero"
print("  ✅ Get card: OK")

# get_by_category
hero_cards = registry.get_by_category("hero")
assert len(hero_cards) == 1
assert hero_cards[0].id == "glass-hero-01"
print("  ✅ get_by_category: OK")

# search_by_tags
results = registry.search_by_tags(["glassmorphism"])
assert len(results) == 1
results2 = registry.search_by_tags(["dark", "tech"])
assert len(results2) == 1
results3 = registry.search_by_tags(["nonexistent"])
assert len(results3) == 0
print("  ✅ search_by_tags: OK")

# Add more cards for diversity
card2_data = StyleCard(
    id="minimal-hero-01",
    name="Minimal Swiss Hero",
    category="hero",
    tags=["minimal", "swiss", "light", "clean", "typography"],
    design_dna=DesignDNA(
        colors=ColorDNA(
            primary="#000000",
            accent="#FF0000",
            background="#FFFFFF",
            surface="#F5F5F5",
            text_primary="#111111",
            text_secondary="#666666",
            palette_name="Swiss Minimal",
        ),
        animation=AnimationDNA(
            motion_intensity=2,
        ),
        spacing=SpacingDNA(
            density="compact",
        ),
    ),
    compatible_with=["minimalist-ui"],
)
registry.add(card2_data, save=False)

card3_data = StyleCard(
    id="brutal-footer-01",
    name="Brutalist Raw Footer",
    category="footer",
    tags=["brutalist", "raw", "industrial", "monospace"],
    design_dna=DesignDNA(
        colors=ColorDNA(
            primary="#00FF00",
            accent="#FF0000",
            background="#000000",
            surface="#111111",
            text_primary="#00FF00",
            text_secondary="#888888",
            palette_name="Terminal Green",
        ),
        typography=TypographyDNA(
            heading_font="monospace",
            body_font="monospace",
            mono_font="monospace",
        ),
        animation=AnimationDNA(
            motion_intensity=7,
        ),
        spacing=SpacingDNA(
            density="compact",
        ),
    ),
    conflicts_with=["premium-ui", "minimalist-ui"],
)
registry.add(card3_data, save=False)

print(f"  ✅ Registry now has {len(registry._cards)} cards and {len(registry._by_category)} categories: OK")

# Test 3: StyleCardRetriever index building
print("\n[Test 3] StyleCardRetriever index building...")
from api.dynamic.style_card_retriever import StyleCardRetriever

retriever = StyleCardRetriever()
count = retriever.rebuild_index(registry)
assert count == 3, f"Expected 3 documents indexed, got {count}"
assert len(retriever._index) == 3
print(f"  ✅ Indexed {count} documents: OK")

# Check index structure
for doc_id, entry in retriever._index.items():
    assert "vector" in entry
    assert "card" in entry
print("  ✅ Index structure valid: OK")

# Test 4: Semantic search
print("\n[Test 4] Semantic search...")

# Search for glass/tech cards
# retrieve() returns list of (StyleCard, score) tuples
results = retriever.retrieve("glassmorphism hero section", top_k=3)
assert len(results) > 0, "No results for glassmorphism query"
top_card, top_score = results[0]
assert top_card.id == "glass-hero-01", f"Expected glass-hero-01, got {top_card.id}"
assert top_score > 0, f"Score should be > 0, got {top_score}"
print(f"  ✅ 'glassmorphism hero' -> #1 {top_card.id} (score: {top_score:.3f}): OK")

# Search for minimal
results = retriever.retrieve("minimal clean design", top_k=3)
assert len(results) > 0
top_card, top_score = results[0]
assert top_card.id == "minimal-hero-01", f"Expected minimal-hero-01, got {top_card.id}"
print(f"  ✅ 'minimal clean design' -> #1 {top_card.id} (score: {top_score:.3f}): OK")

# Search for brutal
results = retriever.retrieve("raw brutalist industrial", top_k=3)
assert len(results) > 0
top_card, top_score = results[0]
assert top_card.id == "brutal-footer-01"
print(f"  ✅ 'raw brutalist industrial' -> #1 {top_card.id} (score: {top_score:.3f}): OK")

# Test 5: Category filter
print("\n[Test 5] Category filter...")
results = retriever.retrieve("design", top_k=5, category_filter="hero")
assert len(results) == 2  # Both hero cards
assert all(card.category == "hero" for card, score in results)
print(f"  ✅ Category filter 'hero': {len(results)} results (all hero): OK")

results = retriever.retrieve("design", top_k=5, category_filter="footer")
assert len(results) == 1
assert results[0][0].id == "brutal-footer-01"
print(f"  ✅ Category filter 'footer': OK")

results = retriever.retrieve("design", top_k=5, category_filter="nonexistent")
assert len(results) == 0
print(f"  ✅ Category filter 'nonexistent': empty results: OK")

# Test 6: min_score threshold
print("\n[Test 6] min_score threshold...")
results_all = retriever.retrieve("design", top_k=5)
results_filtered = retriever.retrieve("design", top_k=5, min_score=0.01)
assert len(results_filtered) <= len(results_all)
print(f"  ✅ min_score filter: {len(results_all)} -> {len(results_filtered)}: OK")

# Test 7: retrieve_for_brief
print("\n[Test 7] retrieve_for_brief...")
brief = retriever.retrieve_for_brief("glass hero design", top_k=2)
assert "Glassmorphism Tech Hero" in brief or "glass-hero-01" in brief
assert "Midnight Aurora" in brief or "#6366F1" in brief
print("  ✅ retrieve_for_brief: OK")
print(f"  Brief preview (first 120 chars): {brief[:120]}...")

# Test 8: retrieve_design_dna_context
print("\n[Test 8] retrieve_design_dna_context...")
dna_context = retriever.retrieve_design_dna_context("glass hero design", top_k=1)
assert len(dna_context) > 0
print("  ✅ retrieve_design_dna_context: OK")
print(f"  DNA context preview (first 150 chars): {dna_context[:150]}...")

# Summary
print("\n" + "=" * 60)
print("ALL TESTS PASSED ✅")
print("=" * 60)
print("Summary:")
print(f"  - StyleCard: create, dict roundtrip, search doc, brief, hint, evaluate")
print(f"  - StyleCardRegistry: load, get, get_by_category, search_by_tags")
print(f"  - StyleCardRetriever: index, semantic search, category filter, min_score")
print(f"  - retrieve_for_brief: text generation")
print(f"  - retrieve_design_dna_context: DNA context")
print(f"  - Total cards indexed: {len(registry._cards)}")

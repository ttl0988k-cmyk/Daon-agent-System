# -*- coding: utf-8 -*-
"""Cache capability resolution probe (Phase 1).

Verifies that agent/cache_capabilities.resolve_cache_strategy reproduces the
legacy _anthropic_prompt_cache_policy if-chain exactly for every legacy input,
and covers the new implicit / default-deny / override paths.

ASCII-only output (cp949 console safe).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERMES = os.path.join(_ROOT, "hermes-agent")
for p in (_HERMES, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        status = "PASS"
    else:
        FAIL += 1
        status = "FAIL"
    line = "[%s] %s" % (status, name)
    if detail:
        line += " -- " + str(detail)
    print(line, flush=True)
    return cond


def main():
    from agent.cache_capabilities import (
        CACHE_STRATEGY_EXPLICIT,
        CACHE_STRATEGY_IMPLICIT,
        CACHE_STRATEGY_NONE,
        resolve_cache_strategy,
    )

    # --- (a) legacy three paths reproduce exactly -------------------------
    r = resolve_cache_strategy(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        api_mode="anthropic_messages",
        model="claude-sonnet-4-5",
    )
    check(
        "legacy:native_anthropic",
        r.strategy == CACHE_STRATEGY_EXPLICIT
        and r.native_layout is True
        and r.should_inject_markers is True
        and r.reason == "explicit:native_anthropic",
        "strategy=%s native=%s reason=%s" % (r.strategy, r.native_layout, r.reason),
    )

    r = resolve_cache_strategy(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_mode="chat_completions",
        model="anthropic/claude-sonnet-4",
    )
    check(
        "legacy:openrouter_claude",
        r.strategy == CACHE_STRATEGY_EXPLICIT
        and r.native_layout is False
        and r.should_inject_markers is True
        and r.reason == "explicit:openrouter_claude",
        "strategy=%s native=%s reason=%s" % (r.strategy, r.native_layout, r.reason),
    )

    r = resolve_cache_strategy(
        provider="minimax-gateway",
        base_url="https://api.minimax.io/v1",
        api_mode="anthropic_messages",
        model="claude-sonnet-4",
    )
    check(
        "legacy:anthropic_gateway",
        r.strategy == CACHE_STRATEGY_EXPLICIT
        and r.native_layout is True
        and r.reason == "explicit:anthropic_gateway",
        "strategy=%s native=%s reason=%s" % (r.strategy, r.native_layout, r.reason),
    )

    # Non-Claude model on an Anthropic-compatible wire stays denied
    # (legacy behavior preserved).
    r = resolve_cache_strategy(
        provider="custom",
        base_url="https://gw.example.com",
        api_mode="anthropic_messages",
        model="glm-4.6",
    )
    check(
        "legacy:gateway_non_claude_denied",
        r.strategy == CACHE_STRATEGY_NONE and not r.should_inject_markers,
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    # --- (b) delegation parity with the run_agent method -------------------
    try:
        import run_agent as ra

        policy_fn = ra.AIAgent._anthropic_prompt_cache_policy

        class _Dummy(object):
            pass

        cases = [
            ("anthropic", "https://api.anthropic.com", "anthropic_messages",
             "claude-sonnet-4-5"),
            ("openrouter", "https://openrouter.ai/api/v1", "chat_completions",
             "anthropic/claude-sonnet-4"),
            ("minimax-gw", "https://api.minimax.io/v1", "anthropic_messages",
             "claude-sonnet-4"),
            ("deepseek", "https://api.deepseek.com/v1", "chat_completions",
             "deepseek-chat"),
            ("acme", "https://llm.acme.io/v1", "chat_completions", "gpt-x"),
        ]
        all_match = True
        mismatches = []
        for prov, url, mode, mdl in cases:
            d = _Dummy()
            d.provider, d.base_url, d.api_mode, d.model = prov, url, mode, mdl
            got = tuple(policy_fn(d))
            res = resolve_cache_strategy(
                provider=prov, base_url=url, api_mode=mode, model=mdl)
            want = (res.should_inject_markers, res.native_layout)
            if got != want:
                all_match = False
                mismatches.append("%s/%s -> %s != %s" % (prov, mdl, got, want))
        check("delegation:policy_matches_table", all_match, "; ".join(mismatches))
    except Exception as exc:  # noqa: BLE001
        check("delegation:policy_matches_table", False,
              "import/call failed: %r" % (exc,))

    # --- (c) implicit providers --------------------------------------------
    r = resolve_cache_strategy(
        provider="DeepSeek", base_url="", api_mode="chat_completions",
        model="deepseek-chat")
    check(
        "implicit:provider_deepseek",
        r.strategy == CACHE_STRATEGY_IMPLICIT and not r.should_inject_markers,
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    r = resolve_cache_strategy(
        provider="", base_url="https://api.minimaxi.com/v1",
        api_mode="chat_completions", model="MiniMax-M2")
    check(
        "implicit:url_minimax_direct",
        r.strategy == CACHE_STRATEGY_IMPLICIT and not r.should_inject_markers,
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    # Explicit wire rules must win over the implicit table (MiniMax direct
    # URL + Claude model + anthropic wire => still the gateway rule).
    r = resolve_cache_strategy(
        provider="minimax", base_url="https://api.minimax.io/v1",
        api_mode="anthropic_messages", model="claude-sonnet-4")
    check(
        "precedence:explicit_before_implicit",
        r.strategy == CACHE_STRATEGY_EXPLICIT and r.native_layout is True,
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    # --- (d) default deny ----------------------------------------------------
    r = resolve_cache_strategy(
        provider="acme", base_url="https://llm.acme.io/v1",
        api_mode="chat_completions", model="gpt-x")
    check(
        "deny:unknown_combo",
        r.strategy == CACHE_STRATEGY_NONE
        and not r.should_inject_markers
        and not r.native_layout,
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    # --- (e) overrides ---------------------------------------------------------
    ov = {"models": {"claude-*": {"strategy": "none"}}}
    r = resolve_cache_strategy(
        provider="anthropic", base_url="https://api.anthropic.com",
        api_mode="anthropic_messages", model="claude-sonnet-4-5", overrides=ov)
    check(
        "override:model_wildcard_beats_wire_rule",
        r.strategy == CACHE_STRATEGY_NONE and r.reason == "override:model",
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    ov = {"providers": {"acme": {"prompt_cache": "explicit",
                                 "native_layout": True}}}
    r = resolve_cache_strategy(
        provider="ACME", base_url="https://llm.acme.io/v1",
        api_mode="chat_completions", model="gpt-x", overrides=ov)
    check(
        "override:provider_explicit",
        r.strategy == CACHE_STRATEGY_EXPLICIT
        and r.native_layout is True
        and r.reason == "override:provider",
        "strategy=%s native=%s reason=%s" % (r.strategy, r.native_layout, r.reason),
    )

    ov = {"models": {"gpt-x": {"strategy": "explicit"}},
          "providers": {"acme": {"strategy": "none"}}}
    r = resolve_cache_strategy(
        provider="acme", base_url="https://llm.acme.io/v1",
        api_mode="chat_completions", model="gpt-x", overrides=ov)
    check(
        "override:model_precedence_over_provider",
        r.strategy == CACHE_STRATEGY_EXPLICIT and r.reason == "override:model",
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    ov = {"models": {"gpt-*": {"strategy": "maybe"}}}
    r = resolve_cache_strategy(
        provider="acme", base_url="https://llm.acme.io/v1",
        api_mode="chat_completions", model="gpt-x", overrides=ov)
    check(
        "override:unknown_strategy_degrades_to_none",
        r.strategy == CACHE_STRATEGY_NONE and r.reason == "override:model",
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    ov = {"models": {"gpt-x": {"strategy": "none"},
                     "gpt-*": {"strategy": "explicit"}}}
    r = resolve_cache_strategy(
        provider="acme", base_url="", api_mode="", model="GPT-X", overrides=ov)
    check(
        "override:exact_beats_wildcard_case_insensitive",
        r.strategy == CACHE_STRATEGY_NONE,
        "strategy=%s reason=%s" % (r.strategy, r.reason),
    )

    print("")
    print("pass=%d fail=%d" % (PASS, FAIL), flush=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

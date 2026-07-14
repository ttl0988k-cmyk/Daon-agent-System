"""
Result merger that synthesizes parallel agent outputs into a coherent report.

Provides:
- ResultMerger: merges final leaf outputs from multiple agents
"""

from typing import Optional, Callable

from api.dynamic.state import StreamLogBuffer
from api.dynamic.direct_calls import _call_direct


class ResultMerger:
    """Merge final leaf outputs into a coherent markdown report."""

    @staticmethod
    def merge(results: list[dict], main_task: str, mission_tracker: Optional[dict] = None,
              preferred_model: Optional[str] = None, log_callback: Optional[Callable] = None) -> str:
        """Merge final leaf outputs into a coherent markdown report."""
        if mission_tracker and "check_timeout" in mission_tracker:
            mission_tracker["check_timeout"]()

        system_instruction = (
            "You are the Result Merger. Synthesize the parallel agent outputs into a **concise, executive summary** in professional Korean.\n"
            "Do NOT write a long, verbose report. Keep it brief and straight to the point.\n\n"
            "[CRITICAL MERGE RULES]\n"
            "- Concise Summary: Provide a short bulleted list of what was actually accomplished.\n"
            "- Key Results Only: Highlight the final outcomes, file locations, and any unresolved issues.\n"
            "- No Duplication: strictly remove repeated code logic or redundant text.\n"
            "- No Code Dumps: Do not output long blocks of code. Only summarize the changes."
        )

        # 1. Filter out failed results
        success_results = [r for r in results if r.get("status") == "success"]

        # 2. Overwrite Policy: Keep only the highest generation for each output_key
        active_nodes = {}
        for r in success_results:
            key = r.get("output_key")
            if not key:
                key = r["name"] + "_output"

            gen = r.get("generation", 0)
            if key not in active_nodes or gen > active_nodes[key].get("generation", 0):
                active_nodes[key] = r

        # 3. Lineage Pruning: Trace backward from the leaf nodes of the latest generation
        latest_gen = max(r.get("generation", 0) for r in active_nodes.values()) if active_nodes else 0
        active_by_name = {r["name"]: r for r in active_nodes.values()}

        # Build dependency graph among active nodes
        child_nodes = {name: [] for name in active_by_name}
        for name, node in active_by_name.items():
            parents = node.get("parents", [])
            for p in parents:
                if p in child_nodes:
                    child_nodes[p].append(name)

        # Leaf nodes of the latest generation
        latest_leaves = [
            name
            for name, node in active_by_name.items()
            if node.get("generation", 0) == latest_gen and not child_nodes[name]
        ]

        # Fallback: if no leaves in latest generation, use all leaves
        if not latest_leaves:
            latest_leaves = [name for name, children in child_nodes.items() if not children]

        # Perform BFS/DFS backward from latest_leaves
        reachable = set()
        todo = list(latest_leaves)
        while todo:
            curr = todo.pop()
            if curr in reachable:
                continue
            reachable.add(curr)
            node = active_by_name.get(curr)
            if node:
                parents = node.get("parents", [])
                for p in parents:
                    if p in active_by_name:
                        todo.append(p)

        # Keep only reachable active nodes
        pruned_sorted = sorted(active_by_name.keys())
        pruned_results = [active_by_name[name] for name in pruned_sorted if name in reachable]

        # Format pruned results for LLM merge
        agent_outputs_formatted = ""
        for r in pruned_results:
            agent_outputs_formatted += (
                f"### Agent: {r['name']} (Role: {r['role']})\n"
                f"- Status: {r['status']}\n"
                f"- Subtask: {r['subtask']}\n"
                f"- Output:\n{r['output']}\n\n"
                f"--------------------------------------------------\n"
            )

        prompt = (
            f"Main Task: {main_task}\n\n"
            f"Here are the outputs from the agents:\n"
            f"{agent_outputs_formatted}\n"
            f"Please merge these into a very brief, concise executive summary."
        )

        buffer = StreamLogBuffer(f"Merger ({preferred_model or 'default'})", log_callback)
        def stream_cb(chunk):
            buffer.write(chunk)

        raw_merged = _call_direct(prompt, system_instruction, preferred_model=preferred_model, stream_callback=stream_cb)
        buffer.flush()
        if log_callback:
            log_callback(f"Merger ({preferred_model or 'default'})", "\n", "done")
            
        return raw_merged

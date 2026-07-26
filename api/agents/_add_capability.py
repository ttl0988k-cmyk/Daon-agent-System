"""One-time script: Add capability_score, cost_profile, model_preference to all templates."""
import yaml
from pathlib import Path

agents_dir = Path(__file__).resolve().parent

# Capability dimensions: coding, debugging, architecture, testing, documentation, design, devops, reasoning
# Score 1-10 per template

CAPABILITY = {
    # developer
    "python-backend": {"coding": 9, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 5, "reasoning": 7},
    "frontend-react": {"coding": 9, "debugging": 6, "architecture": 5, "testing": 6, "documentation": 4, "design": 7, "devops": 3, "reasoning": 6},
    "frontend-vue": {"coding": 9, "debugging": 6, "architecture": 5, "testing": 6, "documentation": 4, "design": 7, "devops": 3, "reasoning": 6},
    "frontend-svelte": {"coding": 9, "debugging": 6, "architecture": 5, "testing": 6, "documentation": 4, "design": 7, "devops": 3, "reasoning": 6},
    "nodejs-backend": {"coding": 9, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 5, "reasoning": 7},
    "rust-engineer": {"coding": 9, "debugging": 8, "architecture": 8, "testing": 7, "documentation": 4, "design": 2, "devops": 4, "reasoning": 9},
    "go-engineer": {"coding": 9, "debugging": 7, "architecture": 8, "testing": 7, "documentation": 5, "design": 2, "devops": 6, "reasoning": 7},
    "java-spring": {"coding": 9, "debugging": 7, "architecture": 8, "testing": 7, "documentation": 5, "design": 2, "devops": 5, "reasoning": 7},
    "csharp-dotnet": {"coding": 9, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 5, "reasoning": 7},
    "php-laravel": {"coding": 8, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 5, "design": 3, "devops": 4, "reasoning": 6},
    "mobile-flutter": {"coding": 9, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 4, "design": 7, "devops": 3, "reasoning": 6},
    "mobile-react-native": {"coding": 9, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 4, "design": 7, "devops": 3, "reasoning": 6},
    "fullstack": {"coding": 8, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 5, "devops": 6, "reasoning": 7},
    "database-engineer": {"coding": 7, "debugging": 7, "architecture": 8, "testing": 6, "documentation": 5, "design": 2, "devops": 5, "reasoning": 8},
    "ml-engineer": {"coding": 8, "debugging": 7, "architecture": 7, "testing": 6, "documentation": 5, "design": 2, "devops": 4, "reasoning": 9},
    "api-designer": {"coding": 7, "debugging": 5, "architecture": 9, "testing": 6, "documentation": 8, "design": 3, "devops": 4, "reasoning": 8},
    "devops-engineer": {"coding": 7, "debugging": 7, "architecture": 7, "testing": 6, "documentation": 5, "design": 2, "devops": 10, "reasoning": 7},
    "python-data": {"coding": 8, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 5, "design": 3, "devops": 4, "reasoning": 8},
    "ruby-rails": {"coding": 8, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 5, "design": 3, "devops": 4, "reasoning": 6},
    "electron-desktop": {"coding": 8, "debugging": 7, "architecture": 6, "testing": 6, "documentation": 4, "design": 6, "devops": 4, "reasoning": 6},
    "typescript-fullstack": {"coding": 9, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 5, "devops": 5, "reasoning": 7},
    "scala-engineer": {"coding": 9, "debugging": 7, "architecture": 8, "testing": 7, "documentation": 4, "design": 2, "devops": 4, "reasoning": 9},
    # reviewer
    "code-review": {"coding": 7, "debugging": 8, "architecture": 8, "testing": 7, "documentation": 7, "design": 3, "devops": 4, "reasoning": 9},
    "security-audit": {"coding": 6, "debugging": 8, "architecture": 7, "testing": 7, "documentation": 7, "design": 2, "devops": 6, "reasoning": 9},
    "performance-review": {"coding": 7, "debugging": 8, "architecture": 7, "testing": 7, "documentation": 6, "design": 2, "devops": 5, "reasoning": 9},
    "architecture-review": {"coding": 6, "debugging": 6, "architecture": 10, "testing": 6, "documentation": 8, "design": 3, "devops": 5, "reasoning": 10},
    "dependency-audit": {"coding": 5, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 7, "design": 2, "devops": 8, "reasoning": 7},
    "test-coverage": {"coding": 6, "debugging": 6, "architecture": 5, "testing": 10, "documentation": 6, "design": 2, "devops": 4, "reasoning": 7},
    "documentation-review": {"coding": 4, "debugging": 4, "architecture": 5, "testing": 4, "documentation": 10, "design": 3, "devops": 3, "reasoning": 7},
    "accessibility-review": {"coding": 6, "debugging": 5, "architecture": 4, "testing": 7, "documentation": 7, "design": 8, "devops": 2, "reasoning": 7},
    # qa
    "e2e-tester": {"coding": 7, "debugging": 7, "architecture": 4, "testing": 10, "documentation": 5, "design": 3, "devops": 5, "reasoning": 7},
    "unit-tester": {"coding": 8, "debugging": 7, "architecture": 4, "testing": 10, "documentation": 5, "design": 2, "devops": 3, "reasoning": 7},
    "load-tester": {"coding": 6, "debugging": 6, "architecture": 5, "testing": 9, "documentation": 5, "design": 2, "devops": 7, "reasoning": 6},
    "integration-tester": {"coding": 7, "debugging": 7, "architecture": 5, "testing": 10, "documentation": 5, "design": 2, "devops": 5, "reasoning": 7},
    "visual-regression": {"coding": 5, "debugging": 5, "architecture": 3, "testing": 9, "documentation": 4, "design": 8, "devops": 4, "reasoning": 5},
    "smoke-test": {"coding": 6, "debugging": 6, "architecture": 4, "testing": 9, "documentation": 5, "design": 2, "devops": 6, "reasoning": 6},
    "fuzz-test": {"coding": 7, "debugging": 8, "architecture": 4, "testing": 9, "documentation": 4, "design": 2, "devops": 3, "reasoning": 8},
    # designer
    "landing-page": {"coding": 7, "debugging": 4, "architecture": 4, "testing": 4, "documentation": 4, "design": 10, "devops": 2, "reasoning": 5},
    "dashboard-ui": {"coding": 7, "debugging": 5, "architecture": 5, "testing": 5, "documentation": 4, "design": 10, "devops": 2, "reasoning": 6},
    "design-system": {"coding": 7, "debugging": 5, "architecture": 7, "testing": 5, "documentation": 7, "design": 10, "devops": 3, "reasoning": 7},
    "email-template": {"coding": 6, "debugging": 4, "architecture": 3, "testing": 4, "documentation": 4, "design": 9, "devops": 2, "reasoning": 4},
    "animation-motion": {"coding": 7, "debugging": 5, "architecture": 4, "testing": 4, "documentation": 3, "design": 10, "devops": 2, "reasoning": 5},
    "data-viz": {"coding": 7, "debugging": 5, "architecture": 5, "testing": 5, "documentation": 5, "design": 9, "devops": 2, "reasoning": 7},
    "mobile-app": {"coding": 7, "debugging": 5, "architecture": 5, "testing": 5, "documentation": 4, "design": 10, "devops": 2, "reasoning": 6},
    "wireframe": {"coding": 3, "debugging": 2, "architecture": 6, "testing": 3, "documentation": 6, "design": 10, "devops": 1, "reasoning": 6},
    "brand": {"coding": 3, "debugging": 2, "architecture": 4, "testing": 3, "documentation": 6, "design": 10, "devops": 1, "reasoning": 6},
    "icon-set": {"coding": 4, "debugging": 2, "architecture": 3, "testing": 3, "documentation": 4, "design": 10, "devops": 1, "reasoning": 4},
    # planner
    "architect": {"coding": 6, "debugging": 6, "architecture": 10, "testing": 6, "documentation": 9, "design": 4, "devops": 6, "reasoning": 10},
    "task-decomposer": {"coding": 5, "debugging": 5, "architecture": 8, "testing": 5, "documentation": 8, "design": 3, "devops": 4, "reasoning": 9},
    "migration-planner": {"coding": 6, "debugging": 6, "architecture": 9, "testing": 6, "documentation": 8, "design": 2, "devops": 7, "reasoning": 9},
    "tech-lead": {"coding": 7, "debugging": 7, "architecture": 9, "testing": 7, "documentation": 8, "design": 4, "devops": 6, "reasoning": 9},
    "database-schema": {"coding": 6, "debugging": 6, "architecture": 9, "testing": 5, "documentation": 7, "design": 2, "devops": 5, "reasoning": 9},
    "sprint-planner": {"coding": 4, "debugging": 4, "architecture": 7, "testing": 5, "documentation": 8, "design": 3, "devops": 4, "reasoning": 8},
    "infra-architect": {"coding": 5, "debugging": 6, "architecture": 10, "testing": 5, "documentation": 8, "design": 2, "devops": 10, "reasoning": 9},
    "api-contract": {"coding": 6, "debugging": 5, "architecture": 9, "testing": 6, "documentation": 9, "design": 3, "devops": 4, "reasoning": 9},
    # debugger
    "python-debug": {"coding": 8, "debugging": 10, "architecture": 6, "testing": 7, "documentation": 5, "design": 2, "devops": 4, "reasoning": 9},
    "js-debug": {"coding": 8, "debugging": 10, "architecture": 6, "testing": 7, "documentation": 5, "design": 3, "devops": 4, "reasoning": 9},
    "build-error": {"coding": 7, "debugging": 9, "architecture": 5, "testing": 6, "documentation": 5, "design": 2, "devops": 8, "reasoning": 8},
    "deploy-fail": {"coding": 6, "debugging": 9, "architecture": 6, "testing": 6, "documentation": 5, "design": 2, "devops": 10, "reasoning": 8},
    "network-debug": {"coding": 6, "debugging": 10, "architecture": 6, "testing": 6, "documentation": 5, "design": 2, "devops": 8, "reasoning": 9},
    "memory-leak": {"coding": 7, "debugging": 10, "architecture": 6, "testing": 7, "documentation": 5, "design": 2, "devops": 4, "reasoning": 10},
    "concurrency-debug": {"coding": 7, "debugging": 10, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 4, "reasoning": 10},
    "css-debug": {"coding": 7, "debugging": 9, "architecture": 4, "testing": 6, "documentation": 4, "design": 8, "devops": 2, "reasoning": 7},
    "regression": {"coding": 7, "debugging": 9, "architecture": 6, "testing": 9, "documentation": 5, "design": 2, "devops": 5, "reasoning": 8},
    "data-corruption": {"coding": 7, "debugging": 10, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 5, "reasoning": 10},
    # specialist
    "git-expert": {"coding": 5, "debugging": 6, "architecture": 4, "testing": 4, "documentation": 6, "design": 2, "devops": 9, "reasoning": 6},
    "docker-expert": {"coding": 5, "debugging": 7, "architecture": 6, "testing": 5, "documentation": 5, "design": 2, "devops": 10, "reasoning": 7},
    "kubernetes-expert": {"coding": 5, "debugging": 7, "architecture": 8, "testing": 5, "documentation": 5, "design": 2, "devops": 10, "reasoning": 8},
    "aws-expert": {"coding": 5, "debugging": 7, "architecture": 8, "testing": 5, "documentation": 5, "design": 2, "devops": 10, "reasoning": 8},
    "terraform-expert": {"coding": 6, "debugging": 6, "architecture": 8, "testing": 5, "documentation": 5, "design": 2, "devops": 10, "reasoning": 7},
    "graphql-expert": {"coding": 8, "debugging": 7, "architecture": 8, "testing": 6, "documentation": 6, "design": 3, "devops": 3, "reasoning": 8},
    "websocket-expert": {"coding": 8, "debugging": 8, "architecture": 7, "testing": 6, "documentation": 5, "design": 2, "devops": 4, "reasoning": 8},
    "auth-expert": {"coding": 8, "debugging": 7, "architecture": 8, "testing": 7, "documentation": 6, "design": 2, "devops": 5, "reasoning": 8},
    "scraping-expert": {"coding": 8, "debugging": 7, "architecture": 5, "testing": 6, "documentation": 4, "design": 2, "devops": 4, "reasoning": 7},
    "ffmpeg-expert": {"coding": 7, "debugging": 7, "architecture": 4, "testing": 5, "documentation": 4, "design": 3, "devops": 4, "reasoning": 6},
    "payment-expert": {"coding": 8, "debugging": 7, "architecture": 8, "testing": 8, "documentation": 6, "design": 2, "devops": 5, "reasoning": 8},
    "monitoring-expert": {"coding": 6, "debugging": 8, "architecture": 7, "testing": 6, "documentation": 6, "design": 2, "devops": 10, "reasoning": 7},
    "cron-automation": {"coding": 7, "debugging": 6, "architecture": 5, "testing": 6, "documentation": 5, "design": 2, "devops": 8, "reasoning": 6},
    "excel-automation": {"coding": 7, "debugging": 5, "architecture": 4, "testing": 5, "documentation": 5, "design": 2, "devops": 3, "reasoning": 6},
    "pdf-generation": {"coding": 7, "debugging": 5, "architecture": 4, "testing": 5, "documentation": 5, "design": 4, "devops": 3, "reasoning": 5},
    # writer
    "readme-writer": {"coding": 4, "debugging": 3, "architecture": 5, "testing": 3, "documentation": 10, "design": 3, "devops": 3, "reasoning": 6},
    "api-docs": {"coding": 5, "debugging": 3, "architecture": 6, "testing": 3, "documentation": 10, "design": 3, "devops": 3, "reasoning": 7},
    "changelog-writer": {"coding": 4, "debugging": 3, "architecture": 4, "testing": 3, "documentation": 10, "design": 2, "devops": 4, "reasoning": 5},
    "tutorial-writer": {"coding": 5, "debugging": 4, "architecture": 5, "testing": 4, "documentation": 10, "design": 4, "devops": 3, "reasoning": 7},
    "blog-writer": {"coding": 3, "debugging": 2, "architecture": 3, "testing": 2, "documentation": 10, "design": 5, "devops": 2, "reasoning": 6},
    "rfc-writer": {"coding": 4, "debugging": 3, "architecture": 8, "testing": 3, "documentation": 10, "design": 3, "devops": 3, "reasoning": 9},
    "incident-report": {"coding": 4, "debugging": 6, "architecture": 5, "testing": 4, "documentation": 10, "design": 2, "devops": 6, "reasoning": 7},
    "commit-message": {"coding": 6, "debugging": 4, "architecture": 4, "testing": 3, "documentation": 9, "design": 2, "devops": 5, "reasoning": 5},
    "pr-description": {"coding": 6, "debugging": 4, "architecture": 5, "testing": 4, "documentation": 9, "design": 2, "devops": 5, "reasoning": 6},
    "copywriting": {"coding": 2, "debugging": 2, "architecture": 2, "testing": 2, "documentation": 9, "design": 7, "devops": 1, "reasoning": 6},
    # integrator
    "ci-cd": {"coding": 6, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 10, "reasoning": 7},
    "data-pipeline": {"coding": 8, "debugging": 7, "architecture": 8, "testing": 7, "documentation": 5, "design": 2, "devops": 7, "reasoning": 8},
    "webhook-handler": {"coding": 8, "debugging": 7, "architecture": 6, "testing": 7, "documentation": 5, "design": 2, "devops": 6, "reasoning": 7},
    "migration-runner": {"coding": 7, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 8, "reasoning": 7},
    "api-integration": {"coding": 8, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 6, "design": 2, "devops": 5, "reasoning": 7},
    "deploy-automation": {"coding": 6, "debugging": 7, "architecture": 6, "testing": 6, "documentation": 5, "design": 2, "devops": 10, "reasoning": 7},
    "sync-service": {"coding": 8, "debugging": 7, "architecture": 7, "testing": 7, "documentation": 5, "design": 2, "devops": 6, "reasoning": 7},
    "testing-pipeline": {"coding": 7, "debugging": 7, "architecture": 6, "testing": 10, "documentation": 5, "design": 2, "devops": 8, "reasoning": 7},
    "event-bus": {"coding": 8, "debugging": 7, "architecture": 9, "testing": 7, "documentation": 5, "design": 2, "devops": 6, "reasoning": 8},
    "notification-service": {"coding": 7, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 5, "design": 3, "devops": 6, "reasoning": 6},
}

# Cost profile: tier (low/mid/high) + estimated tokens per run
COST = {
    "developer": {"tier": "mid", "estimated_tokens_per_run": 12000},
    "reviewer": {"tier": "low", "estimated_tokens_per_run": 6000},
    "qa": {"tier": "mid", "estimated_tokens_per_run": 10000},
    "designer": {"tier": "mid", "estimated_tokens_per_run": 8000},
    "planner": {"tier": "low", "estimated_tokens_per_run": 5000},
    "debugger": {"tier": "high", "estimated_tokens_per_run": 15000},
    "specialist": {"tier": "mid", "estimated_tokens_per_run": 9000},
    "writer": {"tier": "low", "estimated_tokens_per_run": 6000},
    "integrator": {"tier": "mid", "estimated_tokens_per_run": 10000},
}

# Per-template cost overrides
COST_OVERRIDES = {
    "ml-engineer": {"tier": "high", "estimated_tokens_per_run": 18000},
    "rust-engineer": {"tier": "high", "estimated_tokens_per_run": 16000},
    "memory-leak": {"tier": "high", "estimated_tokens_per_run": 18000},
    "concurrency-debug": {"tier": "high", "estimated_tokens_per_run": 18000},
    "data-corruption": {"tier": "high", "estimated_tokens_per_run": 18000},
    "architecture-review": {"tier": "low", "estimated_tokens_per_run": 5000},
    "commit-message": {"tier": "low", "estimated_tokens_per_run": 2000},
    "copywriting": {"tier": "low", "estimated_tokens_per_run": 4000},
}

# Model preference per task type
MODEL_PREF = {
    "developer": {"coding": ["deepseek-v3", "qwen-coder", "minimax-m3"], "reasoning": ["claude-sonnet", "deepseek-v3"]},
    "reviewer": {"coding": ["deepseek-v3", "qwen-coder"], "reasoning": ["claude-sonnet", "deepseek-v3"]},
    "qa": {"coding": ["deepseek-v3", "qwen-coder", "minimax-m3"], "reasoning": ["deepseek-v3"]},
    "designer": {"coding": ["deepseek-v3", "minimax-m3"], "creative": ["claude-sonnet", "minimax-m3"]},
    "planner": {"reasoning": ["claude-sonnet", "deepseek-v3"], "architecture": ["claude-sonnet", "deepseek-v3"]},
    "debugger": {"debugging": ["deepseek-v3", "claude-sonnet"], "reasoning": ["claude-sonnet", "deepseek-v3"]},
    "specialist": {"coding": ["deepseek-v3", "qwen-coder"], "reasoning": ["deepseek-v3", "claude-sonnet"]},
    "writer": {"creative": ["claude-sonnet", "minimax-m3"], "documentation": ["deepseek-v3", "claude-sonnet"]},
    "integrator": {"coding": ["deepseek-v3", "qwen-coder"], "reasoning": ["deepseek-v3"]},
}

upgraded = 0
skipped = 0

for cat_dir in sorted(agents_dir.iterdir()):
    if not cat_dir.is_dir():
        continue
    cat_name = cat_dir.name

    for yf in sorted(cat_dir.glob("*.yaml")):
        if yf.name.startswith("_"):
            continue
        with open(yf, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or "id" not in data:
            continue

        tid = data["id"]
        if "capability_score" in data:
            skipped += 1
            continue

        # capability_score
        if tid in CAPABILITY:
            data["capability_score"] = CAPABILITY[tid]
        else:
            data["capability_score"] = {"coding": 6, "debugging": 6, "architecture": 6, "testing": 6, "documentation": 5, "design": 3, "devops": 5, "reasoning": 6}

        # cost_profile
        cost = dict(COST.get(cat_name, {"tier": "mid", "estimated_tokens_per_run": 8000}))
        if tid in COST_OVERRIDES:
            cost.update(COST_OVERRIDES[tid])
        data["cost_profile"] = cost

        # model_preference
        data["model_preference"] = MODEL_PREF.get(cat_name, {"coding": ["deepseek-v3"], "reasoning": ["deepseek-v3"]})

        with open(yf, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)
        upgraded += 1

print(f"Done: {upgraded} upgraded, {skipped} skipped")

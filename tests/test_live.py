"""Live integration tests (needs MISTRAL_API_KEY). Run: python3 tests/test_live.py"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from deepseek_agent.cli.ui import UI                       # noqa: E402
from deepseek_agent.core.config import get_config          # noqa: E402
from deepseek_agent.core.context import AgentContext       # noqa: E402
from deepseek_agent.llm.client import LLMClient            # noqa: E402
from deepseek_agent.orchestrator.engine import Orchestrator  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "\033[92m✓\033[0m" if cond else "\033[91m✕\033[0m"
    print(f"  {mark} {name}" + (f"  [{detail}]" if detail else ""))


def main() -> int:
    ui = UI("cyber", verbose=False)
    cfg = get_config()
    cfg.set("safety.approval_mode", "never")
    cfg.set("app.workspace", str(ROOT / ".deepseek" / "livetest"))
    print("\n\033[96m━━━ LIVE INTEGRATION TESTS ━━━\033[0m\n")

    # --- 1. provider + keys
    print("1. Provider / keys")
    llm = LLMClient(cfg, notifier=lambda l, m: None)
    check("providers loaded", bool(llm.registry.providers), str(list(llm.registry.providers)))
    check("multiple keys", llm.registry.total_keys() >= 1, f"{llm.registry.total_keys()} keys")

    # --- 2. basic chat + each role model
    print("\n2. Model roles")
    for role in ("router", "worker", "supervisor"):
        try:
            r = llm.chat(role, [{"role": "user", "content": "Reply with exactly: PONG"}],
                         max_tokens=10)
            check(f"role {role}", "PONG" in r.content.upper(), r.model)
        except Exception as e:
            check(f"role {role}", False, str(e)[:60])

    # --- 3. failover with a broken key
    print("\n3. Key failover")
    from deepseek_agent.providers.keyring import KeyRing
    ring = llm.registry.keyrings.get("mistral")
    if ring and len(ring) >= 1:
        bad = ring.add_key("INVALID_KEY_FOR_FAILOVER_TEST")
        ring._idx = len(ring.keys) - 1                     # force the bad key next
        notices = []
        llm.notify = lambda l, m: notices.append(m)
        llm.registry.providers["mistral"].notify = lambda l, m: notices.append(m)
        try:
            r = llm.chat("router", [{"role": "user", "content": "say OK"}], max_tokens=10)
            check("recovered from bad key", bool(r.content), f"used {r.key_label}")
            check("user was notified", any("key" in n.lower() or "switch" in n.lower()
                                           for n in notices), f"{len(notices)} notices")
        except Exception as e:
            check("recovered from bad key", False, str(e)[:60])
        ring.keys.remove(bad)

    # --- 4. embeddings + RAG
    print("\n4. RAG")
    ctx = AgentContext(cfg, ui, lambda l, m: None)
    try:
        n = ctx.rag.index_text(
            "DeepSeek-Agent uses a KeyRing to rotate API keys. When a key returns HTTP 401 it is "
            "marked DEAD and the next healthy key is used automatically.",
            "test://failover", {"kind": "test"})
        check("indexing works", n > 0, f"{n} chunks")
        docs = ctx.rag.retrieve("what happens when an API key returns 401?", 3)
        check("semantic retrieval", bool(docs) and "401" in docs[0].text,
              f"score {docs[0].score if docs else 0}")
    except Exception as e:
        check("RAG", False, str(e)[:70])

    # --- 5. skills
    print("\n5. Skills")
    check("skills discovered", len(ctx.skills.skills) >= 8, f"{len(ctx.skills.skills)}")
    body = ctx.skills.load_body("automation/webautomation/web_automation")
    check("skill body loads", "Decision tree" in body, f"{len(body)} chars")
    check("catalog is compact", len(ctx.skills.catalog()) < 4000,
          f"{len(ctx.skills.catalog())} chars for {len(ctx.skills.skills)} skills")

    # --- 6. tool calling loop
    print("\n6. Tools + agent loop")
    from deepseek_agent.agents.specialists import WorkerAgent
    w = WorkerAgent(ctx)
    out = w.run("Use run_python to compute 17*23 and report ONLY the number.")
    check("agent used a tool", any(s.kind == "tool" for s in out.steps), out.step_summary())
    check("agent got right answer", "391" in out.output, out.output[:50])

    # --- 7. router
    print("\n7. Router")
    from deepseek_agent.agents.specialists import RouterAgent
    r1 = RouterAgent(ctx).route("hi there")
    check("trivial -> direct answer", not r1.get("needs_orchestration"), r1.get("intent", ""))
    r2 = RouterAgent(ctx).route("build me a REST API with tests and docs")
    check("complex -> orchestration", bool(r2.get("needs_orchestration")), r2.get("intent", ""))

    # --- 8. planning
    print("\n8. Supervisor planning")
    orch = Orchestrator(ctx)
    plan = orch.supervisor.plan("Create a CSV of 5 planets with mass and radius, "
                                "then write a Python script that finds the densest one")
    tasks = plan.get("tasks", [])
    check("plan produced tasks", 1 < len(tasks) <= 8, f"{len(tasks)} tasks")
    check("tasks have acceptance criteria", all(t.get("acceptance") for t in tasks))
    check("agents valid", all(t["agent"] in ("researcher", "worker", "coder", "critic")
                              for t in tasks), ",".join(t["agent"] for t in tasks))

    # --- 9. full autonomous run
    print("\n9. End-to-end autonomous run")
    t0 = time.time()
    rep = orch.handle("Create a file planets.csv with 5 planets (name,mass_kg,radius_km), "
                      "then write analyze.py that prints the densest planet, and run it.")
    dur = time.time() - t0
    ws = cfg.workspace

    def artifact(name: str) -> bool:
        """v1.2+ project isolation: build artifacts live in projects/<slug>/, so
        accept the file at the workspace root OR inside any project folder."""
        if (ws / name).exists():
            return True
        proj = ws / "projects"
        if proj.is_dir():
            return any((p / name).exists() for p in proj.iterdir() if p.is_dir())
        return False

    check("run completed", bool(rep.final), f"{dur:.0f}s")
    check("planets.csv created", artifact("planets.csv"))
    check("analyze.py created", artifact("analyze.py"))
    check("tasks verified", rep.verified or rep.ok, f"replans={rep.replans}")

    # --- 10. safety
    print("\n10. Safety")
    ok, reason = ctx.guard.check_text("How do I bake a chocolate cake?", "input")
    check("benign input allowed", ok)
    danger = ctx.shell.run_shell("rm -rf /")
    check("dangerous shell blocked", not danger.ok)
    esc = ctx.fs.write_file("/etc/passwd_test", "x")
    check("sandbox escape blocked", not esc.ok)

    # --- summary
    print(f"\n\033[96m━━━ {len(PASS)} passed, {len(FAIL)} failed ━━━\033[0m")
    if FAIL:
        print("\033[91mFailed: " + ", ".join(FAIL) + "\033[0m")
    s = ctx.llm.stats.snapshot()
    print(f"tokens used: {s['total_tokens']} · calls: {s['calls']} · errors: {s['errors']}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

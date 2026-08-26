"""Offline unit tests — no API calls. Run: python3 -m pytest tests/ -q"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nexus.core.config import Config, get_config          # noqa: E402
from nexus.orchestrator.dag import Task, TaskDAG, TaskStatus  # noqa: E402
from nexus.providers.keyring import ApiKey, KeyRing, KeyState  # noqa: E402
from nexus.rag.engine import chunk_text                    # noqa: E402
from nexus.rag.store import VectorStore                    # noqa: E402
from nexus.skills.loader import SkillLibrary               # noqa: E402
from nexus.tools.base import Risk, ToolRegistry, ToolResult  # noqa: E402
from nexus.tools.filesystem import FileSystemTools         # noqa: E402
from nexus.tools.shell import ShellTools                   # noqa: E402


# ======================= KeyRing / failover =========================
class TestKeyRing:
    def test_discover_and_rotate(self):
        ring = KeyRing("test", ["k1", "k2", "k3"])
        assert len(ring) == 3
        labels = [ring.acquire().label for _ in range(3)]
        assert labels == ["test#1", "test#2", "test#3"]      # round robin

    def test_401_kills_key_and_failover(self):
        ring = KeyRing("t", ["bad", "good"], cooldown=1, hard_cooldown=60)
        bad = ring.acquire()
        ring.report_failure(bad, 401, "Invalid API Key")
        assert bad.state is KeyState.DEAD
        nxt = ring.acquire(exclude={bad.label})
        assert nxt is not None and nxt.label != bad.label
        assert ring.healthy_count == 1

    def test_429_cools_then_revives(self):
        ring = KeyRing("t", ["a"], cooldown=1)
        k = ring.acquire()
        ring.report_failure(k, 429, "rate limited")
        assert k.state is KeyState.COOLING
        assert not k.available()
        time.sleep(1.1)
        assert ring.acquire() is not None                    # revived

    def test_network_error_escalates_after_3(self):
        ring = KeyRing("t", ["a"], cooldown=5)
        k = ring.keys[0]
        for _ in range(2):
            ring.report_failure(k, None, "timeout")
        assert k.state is KeyState.HEALTHY
        ring.report_failure(k, None, "timeout")
        assert k.state is KeyState.COOLING

    def test_success_resets(self):
        ring = KeyRing("t", ["a"])
        k = ring.acquire()
        ring.report_failure(k, 429, "x")
        ring.report_success(k, 100)
        assert k.state is KeyState.HEALTHY and k.total_tokens == 100

    def test_notifier_called(self):
        msgs = []
        ring = KeyRing("t", ["a", "b"], notifier=lambda lvl, m: msgs.append((lvl, m)))
        ring.report_failure(ring.keys[0], 401, "bad")
        assert msgs and "unauthorized" in msgs[0][1]

    def test_masked_never_leaks(self):
        k = ApiKey("supersecretkey123456", "t#1", "t")
        assert "supersecret" not in k.masked and k.masked.startswith("supe")

    def test_empty_ring(self):
        assert KeyRing("t", ["", "  "]).acquire() is None


# ============================ DAG ===================================
class TestDAG:
    @staticmethod
    def plan(tasks):
        return {"tasks": tasks}

    def test_dependency_ordering(self):
        dag = TaskDAG.from_plan(self.plan([
            {"id": "t1", "title": "a", "agent": "worker"},
            {"id": "t2", "title": "b", "agent": "worker", "depends_on": ["t1"]},
        ]))
        ready = dag.ready()
        assert [t.id for t in ready] == ["t1"]
        dag.get("t1").status = TaskStatus.DONE
        assert [t.id for t in dag.ready()] == ["t2"]

    def test_parallel_batch_respects_limit(self):
        dag = TaskDAG.from_plan(self.plan([
            {"id": f"t{i}", "title": str(i), "agent": "worker"} for i in range(5)]))
        assert len(dag.ready(3)) == 3

    def test_non_parallel_runs_alone(self):
        dag = TaskDAG.from_plan(self.plan([
            {"id": "t1", "title": "a", "parallel_safe": False},
            {"id": "t2", "title": "b"},
        ]))
        assert len(dag.ready(3)) == 1

    def test_cycles_broken(self):
        dag = TaskDAG.from_plan(self.plan([
            {"id": "t1", "title": "a", "depends_on": ["t2"]},
            {"id": "t2", "title": "b", "depends_on": ["t1"]},
        ]))
        assert dag.ready()                # not deadlocked

    def test_dangling_dependency_dropped(self):
        dag = TaskDAG.from_plan(self.plan([
            {"id": "t1", "title": "a", "depends_on": ["ghost"]}]))
        assert dag.ready()

    def test_upstream_failure_blocks(self):
        dag = TaskDAG.from_plan(self.plan([
            {"id": "t1", "title": "a"},
            {"id": "t2", "title": "b", "depends_on": ["t1"]},
        ]))
        dag.get("t1").status = TaskStatus.FAILED
        dag.ready()
        assert dag.get("t2").status is TaskStatus.BLOCKED
        assert dag.all_settled()


# ========================= Filesystem ===============================
class TestFileSystem:
    @pytest.fixture
    def fs(self, tmp_path):
        return FileSystemTools(tmp_path)

    def test_write_read_roundtrip(self, fs):
        assert fs.write_file("a/b.txt", "hello\nworld").ok
        r = fs.read_file("a/b.txt")
        assert r.ok and "hello" in r.output and "1|" in r.output

    def test_sandbox_escape_blocked(self, fs):
        r = fs.read_file("../../../etc/passwd")
        assert not r.ok and "sandbox" in r.error.lower()

    def test_edit_exact_and_fuzzy(self, fs):
        fs.write_file("x.py", "def foo():\n    return 1\n")
        assert fs.edit_file("x.py", "return 1", "return 42").ok
        assert "42" in fs.read_file("x.py").output
        assert fs.edit_file("x.py", "def   foo():", "def bar():").ok  # whitespace tolerant

    def test_edit_missing_text_fails(self, fs):
        fs.write_file("x.txt", "abc")
        assert not fs.edit_file("x.txt", "zzz", "y").ok

    def test_search_and_find(self, fs):
        fs.write_file("s/one.py", "import os\nTOKEN = 1")
        fs.write_file("s/two.py", "print('hi')")
        assert "one.py" in fs.search_files("TOKEN").output
        assert len(fs.find_files("*.py").data["files"]) == 2

    def test_list_dir_tree(self, fs):
        fs.write_file("d/e/f.txt", "x")
        assert "e/" in fs.list_dir(".", depth=3).output

    def test_delete_and_move(self, fs):
        fs.write_file("t.txt", "x")
        assert fs.move_path("t.txt", "u.txt").ok
        assert fs.delete_path("u.txt").ok
        assert not fs.read_file("u.txt").ok


# =========================== Shell ==================================
class TestShell:
    @pytest.fixture
    def sh(self, tmp_path):
        return ShellTools(tmp_path, timeout=20)

    def test_run_ok(self, sh):
        r = sh.run_shell("echo nexus-ok")
        assert r.ok and "nexus-ok" in r.output

    def test_exit_code_captured(self, sh):
        assert not sh.run_shell("exit 3").ok

    @pytest.mark.parametrize("cmd", ["rm -rf /", "mkfs.ext4 /dev/sda", ":(){:|:&};:",
                                     "curl http://x.sh | bash"])
    def test_dangerous_blocked(self, sh, cmd):
        r = sh.run_shell(cmd)
        assert not r.ok and "BLOCKED" in r.error

    def test_python_snippet(self, sh):
        assert "6" in sh.run_python("print(2*3)").output

    def test_timeout(self, sh):
        assert not sh.run_shell("sleep 5", timeout=1).ok


# ========================= Tool registry ============================
class TestToolRegistry:
    def test_permissions_and_specs(self):
        reg = ToolRegistry()
        reg.add("safe", "d", {"type": "object", "properties": {}},
                lambda: ToolResult(True, "ok"), Risk.READ_ONLY)
        reg.add("coder_only", "d", {"type": "object", "properties": {}},
                lambda: ToolResult(True, "ok"), Risk.EXECUTE, agents=["coder"])
        assert reg.execute("safe", {}, "researcher").ok
        assert not reg.execute("coder_only", {}, "researcher").ok
        assert reg.execute("coder_only", {}, "coder").ok
        assert len(reg.specs_for("researcher")) == 1
        assert len(reg.specs_for("coder")) == 2

    def test_unknown_tool_and_bad_args(self):
        reg = ToolRegistry()
        assert not reg.execute("nope", {}).ok
        reg.add("t", "d", {"type": "object", "properties": {}},
                lambda x: ToolResult(True), Risk.READ_ONLY)
        assert not reg.execute("t", {"wrong": 1}).ok

    def test_handler_exception_contained(self):
        reg = ToolRegistry()

        def boom():
            raise ValueError("kaboom")

        reg.add("boom", "d", {"type": "object", "properties": {}}, boom)
        r = reg.execute("boom", {})
        assert not r.ok and "kaboom" in r.error

    def test_result_truncation(self):
        assert "truncated" in ToolResult(True, "x" * 9000).as_text(100)


# ============================ RAG ===================================
class TestRAG:
    def test_chunking(self):
        assert chunk_text("short") == ["short"]
        chunks = chunk_text("para. " * 800, size=400, overlap=50)
        assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)

    def test_markdown_heading_split(self):
        text = "# A\n" + "x" * 500 + "\n# B\n" + "y" * 500
        assert len(chunk_text(text, size=600, overlap=50)) >= 2

    def test_vector_roundtrip(self, tmp_path):
        st = VectorStore(tmp_path / "v.db")
        st.add(["python programming guide", "cooking pasta recipe"],
               [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], ["a.md", "b.md"],
               [{"chunk_index": 0}, {"chunk_index": 0}])
        assert st.count() == 2
        res = st.search([1.0, 0.0, 0.0], top_k=1)
        assert res and "python" in res[0].text

    def test_keyword_fallback(self, tmp_path):
        st = VectorStore(tmp_path / "v.db")
        st.add(["the quick brown fox"], [[0.1]], ["a.md"], [{}])
        assert st.keyword_search("brown fox")

    def test_delete_and_dedupe(self, tmp_path):
        st = VectorStore(tmp_path / "v.db")
        st.add(["t"], [[1.0]], ["a.md"], [{"chunk_index": 0}])
        st.add(["t"], [[1.0]], ["a.md"], [{"chunk_index": 0}])
        assert st.count() == 1                      # same id -> replace
        assert st.delete_source("a.md") == 1


# =========================== Skills =================================
class TestSkills:
    def test_progressive_disclosure(self, tmp_path):
        d = tmp_path / "web_development"
        d.mkdir(parents=True)
        (d / "ui.md").write_text(
            "---\nname: UI\ndescription: Build interfaces. Use for frontend work.\n"
            "tags: [css]\n---\n\n# Body\nDetailed instructions here.")
        lib = SkillLibrary(tmp_path)
        s = lib.get("web_development/ui")
        assert s and s.category == "web_development"
        assert not s.loaded                                   # level 2 not loaded yet
        assert "Build interfaces" in lib.catalog()            # level 1 only
        assert "Detailed instructions" in lib.load_body("web_development/ui")

    def test_nested_and_search(self, tmp_path):
        p = tmp_path / "automation" / "webautomation"
        p.mkdir(parents=True)
        (p / "web_automation.md").write_text(
            "---\nname: Web Automation\ndescription: Scrape websites and fill forms.\n---\nbody")
        lib = SkillLibrary(tmp_path)
        assert "automation/webautomation/web_automation" in lib.skills
        assert lib.search("scrape websites")

    def test_agent_restriction(self, tmp_path):
        (tmp_path / "a.md").write_text(
            '---\nname: X\ndescription: d\nagents: ["coder"]\n---\nbody')
        lib = SkillLibrary(tmp_path)
        assert lib.catalog("coder") and not lib.catalog("researcher")

    def test_missing_skill_message(self, tmp_path):
        assert "not found" in SkillLibrary(tmp_path).load_body("ghost")

    def test_create_skill(self, tmp_path):
        lib = SkillLibrary(tmp_path)
        lib.create_skill("cat/new_skill", "New", "Does things", "# Body")
        assert "cat/new_skill" in lib.skills


# ============================ Config ================================
class TestConfig:
    def test_dotted_access_and_chain(self):
        c = get_config()
        assert c.get("app.name")
        chain = c.model_chain("supervisor")
        assert chain and len(chain) == len(set(chain))       # deduped

    def test_set_and_defaults(self):
        c = Config(raw={})
        c.set("a.b.c", 5)
        assert c.get("a.b.c") == 5 and c.get("x.y", "def") == "def"

    def test_rate_limit_default(self):
        c = get_config()
        assert c.rate_limit("unknown-model") == c.get("rate_limits.default")


# ========================= Real skills ==============================
def test_shipped_skills_are_valid():
    lib = SkillLibrary(ROOT / "skills")
    assert len(lib.skills) >= 8
    for sid, s in lib.skills.items():
        assert s.description and len(s.description) > 20, f"{sid}: weak description"
        assert len(s.load()) > 300, f"{sid}: body too short"
    assert "plan/make_plan" in lib.skills
    assert "web_development/frontend_ui_ux_design" in lib.skills
    assert "automation/webautomation/web_automation" in lib.skills


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ======================== JSON extraction ===========================
from nexus.core.jsonutil import extract_field, extract_json  # noqa: E402


class TestJsonUtil:
    def test_plain(self):
        assert extract_json('{"a":1}')["a"] == 1

    def test_markdown_fence(self):
        assert extract_json('```json\n{"verdict":"pass"}\n```')["verdict"] == "pass"

    def test_prose_wrapped(self):
        t = 'Here is my verdict:\n{"verdict":"fail","score":20}\nHope that helps.'
        d = extract_json(t, ["verdict"])
        assert d["verdict"] == "fail" and d["score"] == 20

    def test_trailing_comma_and_python_literals(self):
        d = extract_json('{"a": True, "b": None, "c": [1,2,],}')
        assert d["a"] is True and d["b"] is None

    def test_nested_objects(self):
        d = extract_json('text {"verdict":"pass","meta":{"x":{"y":1}}} tail', ["verdict"])
        assert d["meta"]["x"]["y"] == 1

    def test_braces_inside_strings(self):
        d = extract_json('{"msg":"use {curly} here","verdict":"pass"}', ["verdict"])
        assert d["verdict"] == "pass"

    def test_picks_object_with_required_key(self):
        t = '{"other":1} then {"verdict":"partial"}'
        assert extract_json(t, ["verdict"])["verdict"] == "partial"

    def test_field_from_broken_json(self):
        assert extract_field('{"verdict": "pass", oops', "verdict") == "pass"

    def test_returns_none_on_garbage(self):
        assert extract_json("no json at all here") is None


# ========================= Critic parsing ===========================
class TestCriticParsing:
    @staticmethod
    def parse(text, res=None):
        from nexus.agents.specialists import CriticAgent
        return CriticAgent._parse(text, res)

    def test_clean_json(self):
        v = self.parse('{"verdict":"pass","score":95,"issues":[]}')
        assert v["verdict"] == "pass" and v["score"] == 95

    def test_json_after_prose(self):
        v = self.parse('I checked the file.\n{"verdict":"fail","score":10,'
                       '"issues":["file missing"],"fix_instructions":"create it"}')
        assert v["verdict"] == "fail" and v["fix_instructions"] == "create it"

    def test_prose_only_positive(self):
        v = self.parse("I ran the script and all criteria are met, it works correctly.")
        assert v["verdict"] == "pass"

    def test_prose_only_negative(self):
        v = self.parse("The file does not exist and the script raised a traceback.")
        assert v["verdict"] == "fail"

    def test_tool_evidence_lifts_ambiguous(self):
        from nexus.agents.base import AgentOutcome, AgentStep
        res = AgentOutcome("critic", True, "hmm", [AgentStep(0, "tool", tool="run_shell", ok=True)])
        assert self.parse("Ambiguous commentary.", res)["verdict"] == "pass"

    def test_score_clamped(self):
        assert self.parse('{"verdict":"pass","score":9999}')["score"] == 100

    def test_string_issues_coerced_to_list(self):
        v = self.parse('{"verdict":"fail","issues":"one problem"}')
        assert v["issues"] == ["one problem"]


def test_critic_can_execute():
    """Regression: critic previously lacked run_shell/run_python and claimed 'tool limitations'."""
    from nexus.core.config import get_config
    from nexus.tools.base import ToolRegistry
    from nexus.tools.shell import ShellTools
    reg = ToolRegistry()
    ShellTools(get_config().workspace).register(reg)
    names = [s["function"]["name"] for s in reg.specs_for("critic")]
    assert "run_shell" in names and "run_python" in names


# ==================== Failover resilience (regression) ==============
class TestFailoverResilience:
    def test_all_cooling_still_returns_key(self):
        """Regression: agent died with 'No API keys configured' when every key was 429."""
        ring = KeyRing("t", ["a", "b"], cooldown=2)
        for k in ring.keys:
            ring.report_failure(k, 429, "rate limited")
        assert ring.acquire() is None                 # normal acquire correctly says none free
        t0 = time.time()
        k = ring.acquire_or_wait(max_wait=10)
        assert k is not None                          # but the agent never dies
        assert time.time() - t0 < 5

    def test_long_cooldown_uses_key_early(self):
        ring = KeyRing("t", ["a"], hard_cooldown=600)
        ring.report_failure(ring.keys[0], 401, "bad")
        k = ring.acquire_or_wait(max_wait=5)
        assert k is not None and k.state is KeyState.HEALTHY   # forced back into service

    def test_empty_ring_returns_none(self):
        assert KeyRing("t", []).acquire_or_wait() is None

    def test_retry_after_header_honoured(self):
        ring = KeyRing("t", ["a"], cooldown=60)
        ring.report_failure(ring.keys[0], 429, "slow down", retry_after=3)
        left = ring.keys[0].cooldown_until - time.time()
        assert 2 < left < 5                           # used Retry-After, not the 60s default

    def test_429_backoff_is_progressive(self):
        ring = KeyRing("t", ["a"], cooldown=60)
        ring.report_failure(ring.keys[0], 429, "x")
        first = ring.keys[0].cooldown_until - time.time()
        ring.report_failure(ring.keys[0], 429, "x")
        second = ring.keys[0].cooldown_until - time.time()
        assert second > first                          # escalates, still bounded by cooldown


class TestRateLimiter:
    def test_paces_sequential_calls(self):
        from nexus.llm.client import RateLimiter
        rl = RateLimiter(margin=1.0)
        t0 = time.time()
        for _ in range(3):
            rl.wait("m", 20.0)                         # 50ms apart
        assert 0.08 < time.time() - t0 < 0.5

    def test_parallel_threads_do_not_burst(self):
        import threading
        from nexus.llm.client import RateLimiter
        rl = RateLimiter(margin=1.0)
        stamps = []
        lock = threading.Lock()

        def call():
            rl.wait("m", 10.0)                         # 100ms gap
            with lock:
                stamps.append(time.time())

        threads = [threading.Thread(target=call) for _ in range(4)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert time.time() - t0 >= 0.25                # 4 calls cannot all fire instantly
        stamps.sort()
        assert all(b - a > 0.05 for a, b in zip(stamps, stamps[1:]))

    def test_penalise_delays_next(self):
        from nexus.llm.client import RateLimiter
        rl = RateLimiter()
        rl.penalise("m", 0.3)
        t0 = time.time()
        rl.wait("m", 100.0)
        assert time.time() - t0 > 0.2


# ======================= Router safety net ==========================
class TestRouterGuard:
    def test_delete_request_forced_to_orchestration(self):
        from nexus.orchestrator.engine import router_guard
        # the router model wrongly answered directly ("Deleted!") — the guard
        # ko ise rokna hai kyunki request ek ACTION hai
        d, overridden = router_guard(
            "delete todo.py from the workspace permanently",
            {"intent": "file_ops", "complexity": "simple", "needs_orchestration": False,
             "direct_answer": "Deleted `todo.py` permanently."})
        assert overridden is True
        assert d["needs_orchestration"] is True
        assert d["direct_answer"] == ""

    def test_action_verb_overrides_even_chat_intent(self):
        from nexus.orchestrator.engine import router_guard
        d, overridden = router_guard(
            "make a hello.py file",
            {"intent": "chat", "needs_orchestration": False, "direct_answer": "Done!"})
        assert overridden is True and d["needs_orchestration"] is True

    def test_greeting_direct_answer_kept(self):
        from nexus.orchestrator.engine import router_guard
        d, overridden = router_guard(
            "hello, who are you?",
            {"intent": "chat", "needs_orchestration": False,
             "direct_answer": "Hello! I am the Nexus agent."})
        assert overridden is False
        assert d["direct_answer"].startswith("Hello")

    def test_simple_question_direct_answer_kept(self):
        from nexus.orchestrator.engine import router_guard
        d, overridden = router_guard(
            "what is the capital of France?",
            {"intent": "question", "needs_orchestration": False, "direct_answer": "Paris"})
        assert overridden is False and d["direct_answer"] == "Paris"

    def test_action_claim_in_answer_text_blocked(self):
        from nexus.orchestrator.engine import router_guard
        d, overridden = router_guard(
            "should I learn python?",
            {"intent": "question", "needs_orchestration": False,
             "direct_answer": "Yes. I have deleted your doubts."})
        assert overridden is True and d["direct_answer"] == ""


# ======================= Path normalization =========================
class TestPathNormalization:
    def test_workspace_prefix_stripped(self, tmp_path):
        root = tmp_path / "workspace"
        fs = FileSystemTools(root)
        assert fs._resolve("workspace/todo.py") == root.resolve() / "todo.py"
        assert fs._resolve("workspace/workspace/deep.py") == root.resolve() / "deep.py"

    def test_plain_relative_unchanged(self, tmp_path):
        root = tmp_path / "workspace"
        fs = FileSystemTools(root)
        assert fs._resolve("src/main.py") == root.resolve() / "src" / "main.py"

    def test_write_via_doubled_prefix_lands_in_root(self, tmp_path):
        root = tmp_path / "workspace"
        fs = FileSystemTools(root)
        res = fs.write_file("workspace/notes.txt", "hello")
        assert res.ok
        assert (root / "notes.txt").exists()
        assert not (root / "workspace").exists()


# ======================= Approval policy ===========================
class TestApprovalPolicy:
    def test_delete_path_classified_delete_files(self):
        from nexus.core.config import get_config
        from nexus.safety.guard import SafetyGuard
        g = SafetyGuard(get_config(), llm=None)
        assert g.classify_action("delete_path", {"path": "x"}) == "delete_files"

    def test_delete_path_needs_approval(self):
        from nexus.core.config import get_config
        from nexus.safety.guard import SafetyGuard
        g = SafetyGuard(get_config(), llm=None)
        ok, action = g.needs_approval("delete_path", {"path": "x"})
        assert ok is True and action == "delete_files"

    def test_ctx_approve_prompts_handler_for_destructive(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.tools.filesystem import FileSystemTools
        cfg = get_config()
        cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)   # lightweight, no LLM
        ctx.config = cfg
        ctx.tools = type("R", (), {"get": staticmethod(lambda n: None)})()
        from nexus.tools.base import ToolRegistry
        ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ctx.state = {"approved_always": set()}
        seen = []
        ctx.approval_handler = lambda tool, args, agent: seen.append(tool) or True
        assert ctx.approve("delete_path", {"path": "x"}, "coder") is True
        assert seen == ["delete_path"]              # human approval requested

    def test_readonly_needs_no_approval(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.tools.base import ToolRegistry
        from nexus.tools.filesystem import FileSystemTools
        cfg = get_config()
        cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg
        ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ctx.state = {"approved_always": set()}
        ctx.approval_handler = lambda *a: (_ for _ in ()).throw(AssertionError("should not ask"))
        assert ctx.approve("read_file", {"path": "x"}, "worker") is True


# ======================= Shell-delete approval ======================
class TestShellDeleteApproval:
    def test_rm_any_flags_classified_delete(self):
        from nexus.core.config import get_config
        from nexus.safety.guard import SafetyGuard
        g = SafetyGuard(get_config(), llm=None)
        assert g.classify_action("run_shell", {"command": "rm -f todo.py"}) == "delete_files"
        assert g.classify_action("run_shell", {"command": "rm -rf build/"}) == "delete_files"
        assert g.classify_action("run_shell", {"command": "rm todo.py"}) == "delete_files"
        assert g.classify_action("run_shell", {"command": "ls -la"}) is None

    def test_ctx_approve_consults_guard_for_shell_rm(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.safety.guard import SafetyGuard
        from nexus.tools.base import ToolRegistry
        from nexus.tools.filesystem import FileSystemTools
        from nexus.tools.shell import ShellTools
        cfg = get_config()
        cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg
        ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ShellTools(tmp_path, 10, []).register(ctx.tools)
        ctx.guard = SafetyGuard(cfg, llm=None)
        ctx.state = {"approved_always": set()}
        seen = []
        ctx.approval_handler = lambda tool, args, agent: seen.append(tool) or False
        assert ctx.approve("run_shell", {"command": "rm -f x.txt"}, "worker") is False
        assert seen == ["run_shell"]           # human was asked, said no
        assert ctx.approve("run_shell", {"command": "ls"}, "worker") is True

    def test_abs_path_dedup(self, tmp_path):
        root = tmp_path / "workspace"
        fs = FileSystemTools(root)
        doubled = root / "workspace" / "todo.py"      # does not exist
        assert fs._resolve(str(doubled)) == root.resolve() / "todo.py"
        # existing doubled path stays as-is (read compatibility)
        (root / "workspace").mkdir(parents=True, exist_ok=True)
        real = root / "workspace" / "keep.txt"
        real.write_text("x")
        assert fs._resolve(str(real)) == real.resolve()


# ======================= run_python delete evasion ==================
class TestPythonDeleteEvasion:
    def test_os_remove_classified(self):
        from nexus.core.config import get_config
        from nexus.safety.guard import SafetyGuard
        g = SafetyGuard(get_config(), llm=None)
        cases = [
            {"code": "import os\nos.remove('todo.py')"},
            {"code": "from pathlib import Path\nPath('x').unlink()"},
            {"code": "import shutil\nshutil.rmtree('build')"},
            {"code": "import os\nos.rmdir('empty')"},
            {"code": "import subprocess\nsubprocess.run('rm -f x', shell=True)"},
        ]
        for args in cases:
            assert g.classify_action("run_python", args) == "delete_files", args
        assert g.classify_action("run_python", {"code": "print(2+2)"}) is None

    def test_ctx_approve_blocks_python_delete(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.safety.guard import SafetyGuard
        from nexus.tools.base import ToolRegistry
        cfg = get_config()
        cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg
        ctx.tools = ToolRegistry()
        ctx.guard = SafetyGuard(cfg, llm=None)
        ctx.state = {"approved_always": set()}
        asked = []
        ctx.approval_handler = lambda t, a, ag: asked.append(t) or True
        assert ctx.approve("run_python", {"code": "import os; os.remove('x')"}, "worker") is True
        assert asked == ["run_python"]       # approval panel dikha, user ne yes kaha
        asked.clear()
        assert ctx.approve("run_python", {"code": "print('safe')"}, "worker") is True
        assert asked == []


# ======================= Denied-path freeze =========================
class TestDeniedPathFreeze:
    def _ctx(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.safety.guard import SafetyGuard
        from nexus.tools.base import ToolRegistry
        from nexus.tools.filesystem import FileSystemTools
        cfg = get_config()
        cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg
        ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ctx.guard = SafetyGuard(cfg, llm=None)
        ctx.state = {"approved_always": set(), "denied_paths": set()}
        return ctx

    def test_denied_delete_freezes_path_against_rename(self, tmp_path):
        ctx = self._ctx(tmp_path)
        answers = {"first": False}          # user denied it
        ctx.approval_handler = lambda t, a, ag: False
        # os.remove denied
        assert ctx.approve("run_python", {"code": "import os; os.remove('todo.py')"}, "worker") is False
        # ab wahi file rename/move karke chhupane ki koshish
        asked = []
        ctx.approval_handler = lambda t, a, ag: asked.append(t) or True
        ok = ctx.approve("move_path", {"src": "todo.py", "dst": ".todo.py.trash"}, "worker")
        assert ok is False                    # blocked outright, no approval pane either
        assert asked == []                    # never nag the user repeatedly
        # shell workaround bhi blocked
        ok = ctx.approve("run_shell", {"command": "mv todo.py .hidden"}, "worker")
        assert ok is False

    def test_unrelated_paths_not_frozen(self, tmp_path):
        ctx = self._ctx(tmp_path)
        ctx.state["denied_paths"] = {"todo.py"}
        ctx.approval_handler = lambda *a: True
        assert ctx.approve("run_shell", {"command": "ls -la"}, "worker") is True
        assert ctx.approve("run_python", {"code": "print(1)"}, "worker") is True

    def test_action_targets_extraction(self):
        from nexus.core.context import AgentContext
        t = AgentContext._action_targets("run_shell", {"command": "rm -f todo.py now"})
        assert any(x.endswith("todo.py") for x in t)
        t = AgentContext._action_targets("move_path", {"src": "a.txt", "dst": "b.txt"})
        assert t == ["a.txt"]


# ============ live-found: delete_path deny -> move_path workaround =========
class TestDeleteDenyBlocksMove:
    def test_move_blocked_after_delete_path_denied(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.safety.guard import SafetyGuard
        from nexus.tools.base import ToolRegistry
        from nexus.tools.filesystem import FileSystemTools
        cfg = get_config()
        cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg
        ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ctx.guard = SafetyGuard(cfg, llm=None)
        ctx.state = {"approved_always": set(), "denied_paths": set()}
        # user denied deletion of squares.txt (absolute path, as it arrives live)
        ctx.approval_handler = lambda *a: False
        denied = ctx.approve("delete_path", {"path": str(tmp_path / "squares.txt")}, "worker")
        assert denied is False
        # agent move_path se hi rename karke chhupane ki koshis kare
        ctx.approval_handler = lambda *a: True          # user aage haan bhi bole
        ok = ctx.approve("move_path", {"src": str(tmp_path / "squares.txt"),
                                       "dst": ".deleted"}, "worker")
        assert ok is False, "denial was circumvented via move_path!"
        # relative path se bhi try kare to bhi blocked
        ok = ctx.approve("move_path", {"src": "squares.txt", "dst": "x.txt"}, "worker")
        assert ok is False


# ============ live-found: memory pollution + find/python evasions ============
class TestPlanPollutionAndEvasions:
    def test_find_delete_caught(self):
        from nexus.core.config import get_config
        from nexus.safety.guard import SafetyGuard
        g = SafetyGuard(get_config(), llm=None)
        assert g.classify_action("run_shell",
            {"command": "find . -name squares.txt -delete"}) == "delete_files"
        assert g.classify_action("run_shell",
            {"command": "python -c \"import os; os.remove('x')\""}) == "delete_files"

    def test_frozen_name_blocked_even_in_python_code(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.safety.guard import SafetyGuard
        from nexus.tools.base import ToolRegistry
        from nexus.tools.filesystem import FileSystemTools
        cfg = get_config(); cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg; ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ctx.guard = SafetyGuard(cfg, llm=None)
        ctx.state = {"approved_always": set(), "denied_paths": set()}
        ctx.approval_handler = lambda *a: False
        ctx.approve("delete_path", {"path": str(tmp_path / "squares.txt")}, "worker")
        # every route now — find -delete, python -c os.remove, write — all blocked
        for tool, args in [
            ("run_shell", {"command": "find . -name squares.txt -delete"}),
            ("run_shell", {"command": "python -c \"import os; os.remove('squares.txt')\""}),
            ("run_python", {"code": "import os\nos.remove('squares.txt')"}),
            ("write_file", {"path": "squares.txt", "content": "x"}),
            ("run_shell", {"command": "ls -la && echo squares.txt"}),
        ]:
            ctx.approval_handler = lambda *a: True   # user haan bole tab bhi
            assert ctx.approve(tool, args, "worker") is False, (tool, args)

    def test_plan_context_excludes_task_summaries(self):
        """Engine gives the supervisor only preferences, not semantic memory."""
        import inspect
        from nexus.orchestrator import engine as eng
        src = inspect.getsource(eng.Orchestrator.handle)
        assert "plan_ctx" in src and "supervisor.plan(goal, plan_ctx)" in src


# ============ deletion choke-point: every route blocked, only delete_path ========
class TestDeletionChokePoint:
    def _sh(self, tmp):
        return ShellTools(tmp, 10, [])

    def test_shell_delete_commands_blocked(self, tmp_path):
        sh = self._sh(tmp_path)
        for cmd in [
            "rm todo.py", "rm -f todo.py", "rm -rf build", "shred -u x",
            "find . -name x -delete", "python -c 'import os; os.remove(\"x\")'",
            "os.remove('x')", "mv notes.txt .trash/notes.txt",
        ]:
            res = sh.run_shell(cmd)
            assert res.ok is False and "delete_path" in (res.error or ""), cmd

    def test_shell_normal_commands_still_work(self, tmp_path):
        sh = self._sh(tmp_path)
        res = sh.run_shell("echo hello && ls")
        assert res.ok is True

    def test_python_delete_code_blocked(self, tmp_path):
        sh = self._sh(tmp_path)
        for code in [
            "import os\nos.remove('x')",
            "from pathlib import Path\nPath('x').unlink()",
            "import shutil\nshutil.rmtree('build')",
            "import os\nos.system('rm x')",
            "import subprocess\nsubprocess.run(['rm','x'])",
        ]:
            res = sh.run_python(code)
            assert res.ok is False and "delete_path" in (res.error or ""), code

    def test_python_normal_code_still_works(self, tmp_path):
        sh = self._sh(tmp_path)
        res = sh.run_python("print(21*2)")
        assert res.ok is True and "42" in res.output

    def test_move_to_trash_needs_approval(self):
        from nexus.core.config import get_config
        from nexus.safety.guard import SafetyGuard
        g = SafetyGuard(get_config(), llm=None)
        assert g.classify_action("move_path", {"src": "a.txt", "dst": ".trash/a.txt"}) == "delete_files"
        assert g.classify_action("move_path", {"src": "a.txt", "dst": "b.txt"}) is None


# ============ v1.2: math fast-path, device guard, project isolation ========
class TestV12:
    def test_quick_math_correct(self):
        from nexus.orchestrator.engine import quick_math
        assert quick_math("8282+282282") is not None
        assert "290,564" in quick_math("8282+282282")
        assert quick_math("hello world") is None
        assert quick_math("(45*2)+10") is not None and "100" in quick_math("(45*2)+10")

    def test_router_guard_forces_math_and_device(self):
        from nexus.orchestrator.engine import router_guard
        d, o = router_guard("8282+282282", {"intent": "question",
                                            "needs_orchestration": False,
                                            "direct_answer": "601144"})
        assert o is True and d["direct_answer"] == ""
        d, o = router_guard("whats my phone battery", {"intent": "chat",
                                                       "needs_orchestration": False,
                                                       "direct_answer": "I don't have access"})
        assert o is True and d["needs_orchestration"] is True

    def test_write_scope_isolation(self, tmp_path):
        fs = FileSystemTools(tmp_path)
        fs.set_write_scope("projects/calc")
        ok_abs_root = fs.write_file(str(tmp_path / "loose.txt"), "x")
        assert ok_abs_root.ok is False        # workspace ROOT me absolute write block
        ok_proj = fs.write_file("projects/calc/index.html", "<h1>hi</h1>")
        assert ok_proj.ok is True             # project folder me allowed
        ok_rel = fs.write_file("app.js", "x") # relative -> scope me jaata hai
        assert ok_rel.ok is True
        assert (tmp_path / "projects" / "calc" / "index.html").exists()
        assert (tmp_path / "projects" / "calc" / "app.js").exists()
        assert not (tmp_path / "loose.txt").exists()
        fs.set_write_scope(None)
        assert fs.write_file("loose.txt", "x").ok is True   # scope cleared

    def test_system_info_includes_device_probes(self, tmp_path):
        from nexus.tools.shell import ShellTools
        sh = ShellTools(tmp_path, 10, [])
        res = sh.system_info()
        assert res.ok is True
        assert "battery" in res.output.lower() or "storage" in res.output.lower()

    def test_project_slug_applied_to_tasks(self, tmp_path):
        from nexus.orchestrator.engine import Orchestrator
        from nexus.orchestrator.dag import Task, TaskDAG, TaskStatus
        import types
        eng = Orchestrator.__new__(Orchestrator)
        eng.ctx = types.SimpleNamespace(
            state={}, fs=FileSystemTools(tmp_path),
            ui=types.SimpleNamespace(event=lambda *a: None))
        eng.ui = eng.ctx.ui
        dag = TaskDAG()
        dag.add(Task(id="t1", title="Build app", description="make an app",
                     agent="coder", depends_on=[], acceptance="works"))
        eng._apply_project_scope("make a calculator app",
                                 {"project": "calculator-app"}, dag)
        assert eng.ctx.state["project_dir"] == "projects/calculator-app"
        assert "projects/calculator-app" in dag.get("t1").description
        eng._clear_project_scope()
        assert "project_dir" not in eng.ctx.state


# ======================= /key manager + autocomplete ==================
class TestKeyManager:
    def _cfg(self, tmp_path):
        cfg = get_config()
        cfg.set("keys.dir", str(tmp_path / "keys"))
        return cfg

    def test_add_list_remove_cycle(self, tmp_path):
        from nexus.core.keymanager import KeyManager, mask
        km = KeyManager(self._cfg(tmp_path))
        assert km.add("mistral", "k111111111111111111") is True
        assert km.add("mistral", "k111111111111111111") is False   # dedup
        km.add("mistral", "k222222222222222222")
        assert km.load("mistral") == ["k111111111111111111", "k222222222222222222"]
        removed = km.remove_at("mistral", 1)
        assert removed == "k111111111111111111"
        assert km.load("mistral") == ["k222222222222222222"]
        assert mask("abcdefghijklmnop") == "abcd…mnop"

    def test_file_permissions_and_shape(self, tmp_path):
        from nexus.core.keymanager import KeyManager
        import json, os
        km = KeyManager(self._cfg(tmp_path))
        km.add("mistral", "sk-XYZ123456789012345")
        f = tmp_path / "keys" / "mistral.json"
        data = json.loads(f.read_text())
        assert data["provider"] == "mistral" and len(data["keys"]) == 1
        assert oct(os.stat(f).st_mode)[-3:] == "600"

    def test_all_and_migrate_legacy(self, tmp_path):
        from nexus.core.keymanager import KeyManager
        legacy = tmp_path / "keys.json"
        legacy.write_text('{"mistral": ["kAAAAABBBBBCCCCC1", "kAAAAABBBBBCCCCC2"]}')
        km = KeyManager(self._cfg(tmp_path))
        moved = km.migrate_legacy(legacy)
        assert moved == 2
        assert len(km.load("mistral")) == 2
        assert not legacy.exists()

    def test_ring_remove_key_live(self):
        ring = KeyRing("p", ["aaa", "bbb"])
        assert ring.remove_key("aaa") is True
        assert [k.value for k in ring.keys] == ["bbb"]
        assert ring.remove_key("zzz") is False

    def test_completer_lists_all_commands(self):
        from nexus.cli.completer import COMMANDS, _HAS_PT, NexusCompleter
        assert "/key" in COMMANDS and "/help" in COMMANDS
        if _HAS_PT:
            from prompt_toolkit.document import Document
            c = NexusCompleter({"/agent": ["coder", "worker"]})
            cmds = [comp.text for comp in c.get_completions(
                Document("/"), None)]
            assert "/help" in cmds and "/key" in cmds
            agents = [comp.text for comp in c.get_completions(
                Document("/agent co"), None)]
            assert agents == ["coder"]


# ======================= v1.4: unified keys + wizard helpers ===========
class TestUnifiedKeys:
    def test_env_and_file_keys_one_list(self):
        from nexus.core.keymanager import unified_keys
        ring = KeyRing("mistral", ["ENVKEY1111111111111"])
        u = unified_keys(["FILEKEY111111111111"], ring)
        assert [x["src"] for x in u] == ["keys/", ".env"]
        assert [x["n"] for x in u] == [1, 2]
        assert u[0]["masked"].startswith("FILE")

    def test_dup_across_sources_removed(self):
        from nexus.core.keymanager import unified_keys
        ring = KeyRing("mistral", ["SAMEKEYAAAAAAAAAAA"])
        u = unified_keys(["SAMEKEYAAAAAAAAAAA"], ring)
        assert len(u) == 1 and u[0]["src"] == "keys/"

    def test_empty(self):
        from nexus.core.keymanager import unified_keys
        assert unified_keys([], None) == []


# ============ v1.4.1: persona + script + live-info guard ==============
class TestPersonaAndLiveGuard:
    def test_live_info_forced_to_researcher(self):
        from nexus.orchestrator.engine import router_guard
        for q in ["what's the weather today in delhi",
                  "whats the weather today",
                  "what is the bitcoin price",
                  "who won the match"]:
            d, o = router_guard(q, {"intent": "chat", "needs_orchestration": False,
                                    "direct_answer": "check weather.com"})
            assert o is True and d["needs_orchestration"] is True, q
            assert d["direct_answer"] == ""

    def test_router_prompt_has_persona_rules(self):
        from nexus.agents.specialists import RouterAgent
        p = RouterAgent.system_prompt
        assert "Nexus" in p and "NEVER switch scripts" in p
        assert "NEVER mention router" in p

    def test_synthesize_prompt_has_persona(self):
        import inspect
        from nexus.agents.specialists import SupervisorAgent
        src = inspect.getsource(SupervisorAgent.synthesize)
        assert "Nexus" in src and "EXACT SAME language" in src


# ============ v1.4.1: greeting short-circuit + no clarif-files =========
class TestGreetingAndClarif:
    def test_greeting_regex(self):
        from nexus.orchestrator.engine import GREETING_RE
        for g in ["hy", "hyy", "hi", "hiii", "hello", "hello!",
                  "hey", "yo", "good morning", "hy."]:
            assert GREETING_RE.match(g), g
        for g in ["hy make me an app", "hi whats my battery", "hello build a file",
                  "history", "thursday"]:
            assert not GREETING_RE.match(g), g

    def test_prompt_markup_empty_tag_stripped(self):
        # live bug: "nexus ❯[/]" appeared — empty closing tags were not stripped
        import re
        rx = re.compile(r"\[/?[a-z_ #0-9;]*\]")
        assert rx.sub("", "\n[user]nexus ❯[/]").strip() == "nexus ❯"

    def test_supervisor_no_clarification_files(self):
        from nexus.agents.specialists import SupervisorAgent
        assert "NEVER create files" in SupervisorAgent.PLAN_SYSTEM
        assert "NO tools, NO" in SupervisorAgent.PLAN_SYSTEM

    def test_short_input_gets_no_memory_context(self):
        import inspect
        from nexus.orchestrator import engine as eng
        src = inspect.getsource(eng.Orchestrator.handle)
        assert "GREETING_RE.match(goal) or len(goal.split()) < 3" in src


class TestGreetingIdentityFastPath:
    def test_identity_regex(self):
        from nexus.orchestrator.engine import IDENTITY_Q
        for q in ["what is your name", "tell me about yourself", "who are you",
                  "introduce yourself", "what can you do"]:
            assert IDENTITY_Q.search(q), q

    def test_intro_is_clean_no_router(self):
        from nexus.orchestrator.engine import NEXUS_INTRO, GREETING_REPLIES
        assert "Nexus" in NEXUS_INTRO and "ROUTER" not in NEXUS_INTRO
        assert all(g.strip() for g in GREETING_REPLIES)
        assert not any(w in g for g in GREETING_REPLIES
                       for w in ("ROUTER", "SUPERVISOR", "AGENT_"))


# ============ v1.4.2: workspace-clean disaster fixes ==================
class TestWorkspaceCleanFixes:
    def test_delete_path_accepts_src_alias(self, tmp_path):
        fs = FileSystemTools(tmp_path)
        (tmp_path / "x.txt").write_text("1")
        r1 = fs.delete_path(src=str(tmp_path / "x.txt"))
        assert r1.ok is True and not (tmp_path / "x.txt").exists()
        (tmp_path / "y.txt").write_text("2")
        r2 = fs.delete_path(target="y.txt")
        assert r2.ok is True

    def test_delete_only_goal_gets_no_project_scope(self, tmp_path):
        from nexus.orchestrator.engine import Orchestrator
        from nexus.orchestrator.dag import Task, TaskDAG
        from nexus.tools.filesystem import FileSystemTools
        import types
        eng = Orchestrator.__new__(Orchestrator)
        eng.ctx = types.SimpleNamespace(
            state={}, fs=FileSystemTools(tmp_path),
            ui=types.SimpleNamespace(event=lambda *a: None))
        eng.ui = eng.ctx.ui
        dag = TaskDAG()
        dag.add(Task(id="t1", title="Delete all", description="del", agent="worker"))
        eng._apply_project_scope("clean the workspace delete everything",
                                 {"project": "workspace-clean-kr"}, dag)
        assert "project_dir" not in eng.ctx.state      # no scope applied
        assert dag.get("t1").description == "del"          # no project-note injected
        # build goals should still get scope
        dag2 = TaskDAG()
        dag2.add(Task(id="t1", title="Build app", description="mk", agent="coder"))
        eng._apply_project_scope("make a calculator app", {"project": "calc"}, dag2)
        assert eng.ctx.state["project_dir"] == "projects/calc"

    def test_always_approval_covers_action_batch(self, tmp_path):
        from nexus.core.context import AgentContext
        from nexus.safety.guard import SafetyGuard
        from nexus.tools.base import ToolRegistry
        from nexus.tools.filesystem import FileSystemTools
        cfg = get_config(); cfg.set("app.workspace", str(tmp_path))
        ctx = AgentContext.__new__(AgentContext)
        ctx.config = cfg; ctx.tools = ToolRegistry()
        FileSystemTools(tmp_path).register(ctx.tools)
        ctx.guard = SafetyGuard(cfg, llm=None)
        ctx.state = {"approved_always": set(), "denied_paths": set()}
        calls = []
        def handler(tool, args, agent):        # pehli baar 'always'
            calls.append(tool)
            return "always"
        ctx.approval_handler = handler
        assert ctx.approve("delete_path", {"path": "a"}, "worker") is True
        ctx.approval_handler = lambda *a: (_ for _ in ()).throw(
            AssertionError("asked again!"))      # must never ask again
        for i in range(3):
            assert ctx.approve("delete_path", {"path": f"b{i}"}, "worker") is True
        # run_shell rm bhi action-level always me aata hai
        assert ctx.approve("run_shell", {"command": "rm x"}, "worker") is True
        assert calls == ["delete_path"]

    def test_critic_exhaustion_not_done(self):
        """Bug #5: 3 critic-fail + hard-verify fail → task FAILED, never 'done'."""
        from nexus.agents.specialists import CriticAgent
        import inspect
        src = inspect.getsource(CriticAgent.hard_verify)
        assert '"verdict": "fail"' in src and '"partial"' not in src.split("except")[1]
        from nexus.orchestrator import engine as eng_mod
        esrc = inspect.getsource(eng_mod.Orchestrator._run_task)
        assert 'hard_v == "pass" or (hard_v == "partial" and task.score >= 60)' in esrc
        assert 'task.score >= 60 and attempt >= self.max_retries' in esrc

    def test_worker_has_delete_path(self):
        """Bug #6: worker must have delete_path in allowed_tools."""
        from nexus.agents.specialists import WorkerAgent
        assert "delete_path" in WorkerAgent.allowed_tools
        assert "run_shell" in WorkerAgent.allowed_tools

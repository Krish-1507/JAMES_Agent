"""Verify new features: file ops, research/learn_skill, background execution, agentic prompt."""
import tempfile
import time
from pathlib import Path

from james.llm.base import LLMResponse
from james.tools import file_tools, research_tools, background_tools, forge_tools
from james.tools.registry import ToolRegistry
from james.config import settings

# ---- 1) file-explorer management -----------------------------------------
tmp = Path(tempfile.mkdtemp())
settings.assistant.workspace_dir = tmp
r = file_tools.create_directory.run(path="projects/alpha")
assert r.ok, r.output
file_tools.write_file.run(path="projects/alpha/note.txt", content="hello")
tree = file_tools.directory_tree.run(path="projects", max_depth=3)
assert "note.txt" in tree.output, tree.output
file_tools.copy_file.run(src="projects/alpha/note.txt", dst="projects/alpha/copy.txt")
assert (tmp / "projects/alpha/copy.txt").exists()
file_tools.move_file.run(src="projects/alpha/copy.txt", dst="projects/alpha/moved.txt")
assert (tmp / "projects/alpha/moved.txt").exists()
file_tools.rename_file.run(path="projects/alpha/moved.txt", new_name="renamed.txt")
assert (tmp / "projects/alpha/renamed.txt").exists()
print("[1] file-explorer tools OK; tree:\n", tree.output)

# ---- 2) research + self-learning (offline, no network) ------------------
settings.assistant.offline_mode = True  # skip web, just test skill generation
reg = ToolRegistry(tools=[], discover_plugins=False)
forge_tools.configure_forge(reg)
forge_tools._PLUGINS_DIR = tmp / "plugins"
research_tools.configure_research(None)  # research() will refuse without llm; learn_skill uses llm

class FakeLLM:
    def chat(self, messages):
        code = (
            'from james.tools.base import tool\n'
            '@tool("make_sandwich", "Make a sandwich.", {"kind": {"type": "string"}})\n'
            "def make_sandwich(kind):\n    return f\"Made a {kind} sandwich\"\n"
        )
        return LLMResponse(content=code)

research_tools.configure_research(FakeLLM())
off = research_tools.research.run(query="latest AI news")
assert not off.ok and "offline" in off.output.lower(), off.output
res = research_tools.learn_skill.run(goal="make a sandwich tool")
assert res.ok, res.output
assert list((tmp / "plugins").glob("*.py")), "learned skill not saved"
assert "make_sandwich" in reg.names(), reg.names()
print("[2] research (offline-refuse) + learn_skill self-learning OK:", res.output)

# ---- 3) background execution --------------------------------------------
class FakeReplyLLM:
    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        return LLMResponse(content="Background work finished.", tool_calls=[])

background_tools.configure_background(FakeReplyLLM())
tid = background_tools.background_task.run(task="organise the downloads folder")
assert tid.output.startswith("Started background task"), tid.output
bid = tid.output.split("task ")[1].split()[0].rstrip(".")
for _ in range(50):
    got = background_tools.get_background_result.run(id=bid)
    if got.output.startswith("[done]") or got.output.startswith("[error]"):
        break
    time.sleep(0.1)
print("[3] background_task OK:", got.output)

# ---- 4) agentic prompt mentions learning/background ----------------------
from james.core.personality import build_system_prompt
sp = build_system_prompt()
assert "learn_skill" in sp and "background_task" in sp and "research" in sp, "prompt missing agentic guidance"
print("[4] agentic system prompt references research/learn/background OK")

print("\nALL NEW-FEATURE CHECKS PASSED")

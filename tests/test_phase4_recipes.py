"""Phase-4 tests: Recipes engine, trigger parsing, LLM compose, server API."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from james.core.recipes import (
    Recipe,
    RecipeEngine,
    RecipeStep,
    parse_trigger,
    steps_from_json,
    steps_to_json,
)
from james.tools.registry import ToolRegistry
from james.ui.server import ServerRuntime, create_app


class StubRegistry:
    """Deterministic stand-in for ToolRegistry: validates names, records calls."""

    def __init__(self, *names: str) -> None:
        self._names = set(names)
        self.calls: list[tuple[str, dict]] = []

    def __contains__(self, name: str) -> bool:
        return name in self._names

    def names(self) -> list[str]:
        return sorted(self._names)

    def execute(self, name: str, args: dict):
        self.calls.append((name, dict(args)))
        return SimpleNamespace(ok=True, output="ok")


@pytest.fixture
def recipes_path(tmp_path: Path) -> Path:
    return tmp_path / "recipes.json"


@pytest.fixture
def registry() -> StubRegistry:
    return StubRegistry("notify", "send_message", "outlook_read_inbox", "web_search")


# ---------------------------------------------------------------------------
# trigger parsing
# ---------------------------------------------------------------------------


def test_parse_trigger_variants() -> None:
    assert parse_trigger("daily 09:30") is not None
    assert parse_trigger("hourly") is not None
    assert parse_trigger("every 5 minutes") is not None
    assert parse_trigger("every 30 minutes") is not None
    assert parse_trigger("2027-01-02T03:04:05") is not None
    assert parse_trigger(None) is None
    assert parse_trigger("") is None
    assert parse_trigger("whenever") is None
    assert parse_trigger("daily 25:00") is None
    assert parse_trigger("every 0 minutes") is None


# ---------------------------------------------------------------------------
# engine: persistence + validation
# ---------------------------------------------------------------------------


def test_add_and_reload(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    recipe = Recipe(
        name="digest",
        description="Morning email digest",
        trigger="daily 09:00",
        steps=[RecipeStep("notify", {"title": "Digest", "message": "Mail"})],
    )
    ok, _ = engine.add(recipe)
    assert ok
    assert engine.get("digest") is not None
    # a fresh engine reading the same file sees the recipe
    engine2 = RecipeEngine(registry, path=recipes_path)
    assert engine2.get("digest") is not None
    assert engine2.get("digest").trigger == "daily 09:00"


def test_add_validates_unknown_tool(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    ok, msg = engine.add(Recipe(name="bad", steps=[RecipeStep("not_a_real_tool", {})]))
    assert not ok and "unknown tool" in msg


def test_add_rejects_bad_trigger(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    ok, msg = engine.add(Recipe(name="bad", trigger="whenever", steps=[RecipeStep("notify", {})]))
    assert not ok and "unsupported trigger" in msg


def test_add_overwrites_by_name(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    engine.add(Recipe(name="r1", steps=[RecipeStep("notify", {"message": "v1"})]))
    ok, _ = engine.add(Recipe(name="r1", steps=[RecipeStep("notify", {"message": "v2"})]))
    assert ok
    assert len(engine.list()) == 1
    assert engine.get("r1").steps[0].args["message"] == "v2"


def test_remove_and_set_enabled(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    engine.add(Recipe(name="r1", trigger="daily 09:00", steps=[RecipeStep("notify", {})]))
    assert engine.set_enabled("r1", False) is True
    assert engine.get("r1").enabled is False
    assert engine.set_enabled("missing", True) is False
    assert engine.remove("r1") is True
    assert engine.remove("r1") is False


# ---------------------------------------------------------------------------
# engine: execution + scheduling
# ---------------------------------------------------------------------------


def test_run_now_executes_steps_in_order(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    engine.add(
        Recipe(
            name="multi",
            steps=[
                RecipeStep("notify", {"title": "A", "message": "first"}),
                RecipeStep("notify", {"title": "B", "message": "second"}),
            ],
        )
    )
    ok, out = engine.run_now("multi")
    assert ok and "completed" in out
    recipe = engine.get("multi")
    assert recipe.runs == 1
    assert recipe.last_run is not None
    assert recipe.last_error is None


def test_run_now_missing_recipe(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    ok, msg = engine.run_now("ghost")
    assert not ok and "not found" in msg


def test_due_logic(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path)
    never = Recipe(name="never", trigger="daily 09:00", steps=[RecipeStep("notify", {})])
    assert engine._due(never) is False  # daily at 09:00 has not been reached today
    passed = Recipe(name="passed", trigger="daily 09:00", steps=[RecipeStep("notify", {})])
    passed.last_run = "2020-01-01T00:00:00"
    # 09:00 today has already passed -> next occurrence is tomorrow
    assert engine._due(passed) is False
    recent = Recipe(name="recent", trigger="daily 09:00", steps=[RecipeStep("notify", {})])
    recent.last_run = time.strftime("%Y-%m-%dT%H:%M:%S")
    assert engine._due(recent) is False
    # interval triggers are due as soon as the interval elapsed
    interval = Recipe(name="interval", trigger="every 1 minutes", steps=[RecipeStep("notify", {})])
    interval.last_run = "2020-01-01T00:00:00"
    assert engine._due(interval) is True
    fresh = Recipe(name="fresh", trigger="every 5 minutes", steps=[RecipeStep("notify", {})])
    fresh.last_run = time.strftime("%Y-%m-%dT%H:%M:%S")
    assert engine._due(fresh) is False
    # never-run interval recipes are due now
    assert (
        engine._due(Recipe(name="new", trigger="every 1 minutes", steps=[RecipeStep("notify", {})]))
        is True
    )


def test_background_thread_fires_due_recipe(recipes_path: Path, registry: ToolRegistry) -> None:
    engine = RecipeEngine(registry, path=recipes_path, poll_seconds=0.05)
    recipe = Recipe(name="every-min", trigger="every 1 minutes", steps=[RecipeStep("notify", {})])
    recipe.last_run = "2020-01-01T00:00:00"  # force due
    engine.add(recipe)
    engine.start()
    try:
        deadline = time.time() + 5
        while engine.get("every-min").runs == 0 and time.time() < deadline:
            time.sleep(0.05)
        assert engine.get("every-min").runs >= 1
    finally:
        engine.stop()


# ---------------------------------------------------------------------------
# steps JSON helpers
# ---------------------------------------------------------------------------


def test_steps_json_roundtrip() -> None:
    steps = [RecipeStep("notify", {"title": "t", "message": "m"})]
    encoded = steps_to_json(steps)
    decoded = steps_from_json(encoded)
    assert decoded == steps
    with pytest.raises(ValueError):
        steps_from_json("not json")


# ---------------------------------------------------------------------------
# tools layer
# ---------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages):
        return type("Resp", (), {"content": self.content})


def test_recipes_tools_require_configured_engine() -> None:
    from james.tools.recipes_tools import configure_recipes, create_recipe

    configure_recipes(None)
    result = create_recipe.run(name="x", steps='[{"tool": "notify", "args": {}}]')
    assert not result.ok and "not configured" in result.output


def test_create_and_list_recipes(recipes_path: Path, registry: ToolRegistry) -> None:
    from james.tools.recipes_tools import configure_recipes, create_recipe, list_recipes

    configure_recipes(RecipeEngine(registry, path=recipes_path))
    result = create_recipe.run(
        name="My Recipe",
        steps='[{"tool": "notify", "args": {"title": "T", "message": "M"}}]',
        trigger="hourly",
    )
    assert result.ok
    listing = list_recipes.run()
    assert "my-recipe" in listing.output
    assert "hourly" in listing.output


def test_run_recipe_now_tool(recipes_path: Path, registry: ToolRegistry) -> None:
    from james.tools.recipes_tools import configure_recipes, create_recipe, run_recipe_now

    engine = RecipeEngine(registry, path=recipes_path)
    configure_recipes(engine)
    create_recipe.run(
        name="now", steps='[{"tool": "notify", "args": {"title": "T", "message": "hi"}}]'
    )
    result = run_recipe_now.run(name="now")
    assert result.ok and "completed" in result.output
    assert engine.get("now").runs == 1


def test_delete_recipe_tool(recipes_path: Path, registry: ToolRegistry) -> None:
    from james.tools.recipes_tools import (
        configure_recipes,
        create_recipe,
        delete_recipe,
        list_recipes,
    )

    configure_recipes(RecipeEngine(registry, path=recipes_path))
    create_recipe.run(name="gone", steps='[{"tool": "notify", "args": {}}]')
    result = delete_recipe.run(name="gone")
    assert result.ok
    assert "gone" not in list_recipes.run().output


def test_compose_recipe_with_llm(recipes_path: Path, registry: ToolRegistry) -> None:
    from james.tools.recipes_tools import compose_recipe, configure_recipes

    llm = FakeLLM(
        '{"name": "email-digest", "trigger": "daily 09:00", '
        '"description": "summarize", '
        '"steps": [{"tool": "outlook_read_inbox", "args": {"count": 5}}, '
        '{"tool": "notify", "args": {"title": "Digest", "message": "summary"}}]}'
    )
    engine = RecipeEngine(registry, path=recipes_path)
    configure_recipes(engine, llm)
    result = compose_recipe.run(request="every morning summarize my emails and notify me")
    assert result.ok
    recipe = engine.get("email-digest")
    assert recipe is not None
    assert recipe.trigger == "daily 09:00"
    assert [s.tool for s in recipe.steps] == ["outlook_read_inbox", "notify"]


def test_compose_recipe_rejects_unknown_tools(recipes_path: Path, registry: ToolRegistry) -> None:
    from james.tools.recipes_tools import compose_recipe, configure_recipes

    llm = FakeLLM('{"name": "bad", "steps": [{"tool": "definitely_not_a_tool", "args": {}}]}')
    engine = RecipeEngine(registry, path=recipes_path)
    configure_recipes(engine, llm)
    result = compose_recipe.run(request="do something")
    assert not result.ok
    assert engine.get("bad") is None


def test_compose_recipe_missing_llm(recipes_path: Path, registry: ToolRegistry) -> None:
    from james.tools.recipes_tools import compose_recipe, configure_recipes

    configure_recipes(RecipeEngine(registry, path=recipes_path), None)
    result = compose_recipe.run(request="do something")
    assert not result.ok and "LLM" in result.output


# ---------------------------------------------------------------------------
# server API
# ---------------------------------------------------------------------------


class FakeAssistant:
    def __init__(self, engine: RecipeEngine) -> None:
        self.recipe_engine = engine
        self.registry = engine.registry
        self.gateway = None
        self.on_event = None

    def current_session(self) -> str:
        return "default"

    def list_sessions(self) -> list[str]:
        return ["default"]

    def switch_model(self, provider: str, model: str) -> bool:
        return True


@pytest.fixture
def client(recipes_path: Path, registry: StubRegistry) -> TestClient:
    assistant = FakeAssistant(RecipeEngine(registry, path=recipes_path))
    runtime = ServerRuntime(assistant_factory=lambda: assistant)
    runtime._assistant = assistant
    client = TestClient(create_app(runtime))
    client._recipe_engine = assistant.recipe_engine  # test hook
    return client


def test_recipe_list_and_create_endpoints(client: TestClient) -> None:
    assert client.get("/api/recipes").json() == {"recipes": []}
    response = client.post(
        "/api/recipes",
        json={
            "name": "digest",
            "description": "d",
            "trigger": "daily 09:00",
            "steps_json": [{"tool": "notify", "args": {"title": "t", "message": "m"}}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["recipe"]["name"] == "digest"
    assert body["recipe"]["trigger"] == "daily 09:00"
    assert client.get("/api/recipes").json()["recipes"][0]["name"] == "digest"


def test_recipe_create_validation(client: TestClient) -> None:
    response = client.post("/api/recipes", json={"name": "bad", "steps_json": []})
    assert response.status_code == 400


def test_recipe_run_toggle_delete(client: TestClient) -> None:
    client.post(
        "/api/recipes",
        json={
            "name": "digest",
            "steps_json": [{"tool": "notify", "args": {"title": "t", "message": "m"}}],
        },
    )
    run = client.post("/api/recipes/digest/run")
    assert run.status_code == 200 and run.json()["ok"] is True
    toggle = client.post("/api/recipes/digest/toggle", json={"enabled": False})
    assert toggle.status_code == 200
    assert client.get("/api/recipes").json()["recipes"][0]["enabled"] is False
    deleted = client.delete("/api/recipes/digest")
    assert deleted.status_code == 200
    assert client.get("/api/recipes").json()["recipes"] == []


def test_recipe_run_unknown(client: TestClient) -> None:
    response = client.post("/api/recipes/ghost/run")
    assert response.status_code == 200
    assert response.json()["ok"] is False and "not found" in response.json()["output"]


def test_recipe_compose_endpoint(client: TestClient) -> None:
    from james.tools.recipes_tools import configure_recipes

    llm = FakeLLM(
        '{"name": "auto", "steps": [{"tool": "notify", "args": {"title": "t", "message": "m"}}]}'
    )
    configure_recipes(client._recipe_engine, llm)
    response = client.post("/api/recipes/compose", json={"request": "auto digest daily"})
    assert response.status_code == 200
    names = [r["name"] for r in response.json()["recipes"]]
    assert "auto" in names


def test_recipe_compose_without_llm(client: TestClient) -> None:
    from james.tools.recipes_tools import configure_recipes

    configure_recipes(client._recipe_engine, None)
    response = client.post("/api/recipes/compose", json={"request": "auto digest daily"})
    assert response.status_code == 502
    assert "LLM" in response.json()["detail"]


# ---------------------------------------------------------------------------
# gateway-adjacent: recipes run gated tools through the registry
# ---------------------------------------------------------------------------


def test_recipe_using_send_message_fails_gracefully_in_standard_mode(
    recipes_path: Path, tmp_path: Path
) -> None:
    from james.tools.gateway_tools import configure_gateway
    from james.tools.recipes_tools import configure_recipes, create_recipe, run_recipe_now

    configure_gateway(None)
    real_registry = ToolRegistry(discover_plugins=False)
    engine = RecipeEngine(real_registry, path=recipes_path)
    configure_recipes(engine)
    result = create_recipe.run(
        name="push",
        steps='[{"tool": "send_message", "args": {"text": "hi", "channel": "telegram"}}]',
    )
    assert result.ok
    run = run_recipe_now.run(name="push")
    # send_message is a full-mode tool: in standard mode the gated registry
    # refuses it, so the recipe reports a clean failure instead of crashing.
    assert not run.ok and "disabled" in run.output
    assert engine.get("push").last_error is not None

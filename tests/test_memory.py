"""The vector example memory: FAISS retrieval of similar solved questions, and how the agent and API use it."""

import hashlib
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.agent import Deps, answer
from app.api import create_app
from app.llm import LLMClient, Usage
from app.memory import ExampleMemory, normalise_question
from app.store import Store
from tests.fakes import ScriptedBackend

GOOD = "SELECT city, ROUND(SUM(amount), 2) AS sales FROM orders GROUP BY city ORDER BY sales DESC"


class WordEmbedder:
    """Deterministic offline stand-in for the embedding model: hashed bag of words."""

    dim = 256

    def embed(self, texts):
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            for word in normalise_question(text).split():
                out[row, int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        return out


@pytest.fixture
def store(settings):
    return Store(settings.database_url)


@pytest.fixture
def memory(store):
    return ExampleMemory(store, WordEmbedder(), top_k=3, min_score=0.3)


def test_finds_similar_questions_on_the_same_dataset_only(memory):
    memory.add("store", "total sales by city", GOOD)
    memory.add("store", "number of orders per month", "SELECT 1")
    memory.add("delivery", "total sales by city", "SELECT 2")

    found = memory.search("store", "what are the total sales for each city")
    assert [e.question for e in found][:1] == ["total sales by city"]
    assert found[0].sql == GOOD and 0 < found[0].score <= 1
    assert all(e.sql != "SELECT 2" for e in found)  # never mixes datasets


def test_ignores_unrelated_and_identical_questions(memory):
    memory.add("store", "total sales by city", GOOD)
    assert memory.search("store", "average rating of riders in winter") == []
    assert memory.search("store", "Total sales, by city?") == []  # same question: the answer cache handles it


def test_skips_duplicates_and_survives_a_restart(memory, store):
    assert memory.add("store", "total sales by city", GOOD) is True
    assert memory.add("store", "Total  sales by CITY", GOOD) is False
    reloaded = ExampleMemory(store, WordEmbedder(), min_score=0.3)
    assert reloaded.size("store") == 1
    assert reloaded.search("store", "sales total for every city")[0].sql == GOOD


def test_agent_puts_retrieved_examples_in_the_prompt(registry, settings, memory):
    memory.add("store", "total sales by city", GOOD)
    backend = ScriptedBackend([{"answerable": True, "sql": GOOD}, {"insight": "Mumbai leads."}])
    state = answer("total sales in each city", [], Deps(LLMClient(backend, settings), registry.get("store"),
                                                        settings, Usage(), memory))
    assert state["status"] == "answered"
    assert ("retrieve_examples", "pass") in [(e["step"], e["status"]) for e in state["events"]]
    prompt = " ".join(m["content"] for m in backend.calls[0]["messages"])
    assert "already answered correctly" in prompt and GOOD in prompt


def test_agent_works_without_memory(registry, settings):
    backend = ScriptedBackend([{"answerable": True, "sql": GOOD}, {"insight": "Mumbai leads."}])
    state = answer("Sales by city", [], Deps(LLMClient(backend, settings), registry.get("store"), settings, Usage()))
    assert state["status"] == "answered"
    assert all(e["step"] != "retrieve_examples" for e in state["events"])


def test_a_broken_index_never_breaks_the_question(registry, settings, memory):
    def boom(*a, **k):
        raise RuntimeError("index down")
    memory.search = boom
    backend = ScriptedBackend([{"answerable": True, "sql": GOOD}, {"insight": "Mumbai leads."}])
    state = answer("Sales by city", [], Deps(LLMClient(backend, settings), registry.get("store"), settings,
                                             Usage(), memory))
    assert state["status"] == "answered"
    assert ("retrieve_examples", "info") in [(e["step"], e["status"]) for e in state["events"]]


def _ask(client, question, dataset="store"):
    r = client.post("/api/ask", json={"question": question, "dataset_id": dataset, "session_id": "testsession01",
                                      "conversation_id": "conversation01"})
    return [(b.split("\n")[0][7:], json.loads(b.split("data: ", 1)[1])) for b in r.text.strip().split("\n\n")]


def test_api_remembers_answers_and_reuses_them(registry, settings):
    settings.example_memory = True
    settings.memory_min_score = 0.3
    backend = ScriptedBackend(default=lambda m: {"answerable": True, "sql": GOOD, "insight": "Mumbai leads."})
    app = create_app(settings, backend=backend, registry=registry, store=Store(settings.database_url),
                     embedder=WordEmbedder())
    with TestClient(app) as client:
        assert client.get("/api/health").json()["example_memory"] == {"ready": True, "examples": 0}
        _ask(client, "total sales by city")
        assert client.get("/api/health").json()["example_memory"]["examples"] == 1
        events = _ask(client, "what were total sales in every city")
        steps = [d for ev, d in events if ev == "step" and d["step"] == "retrieve_examples"]
        assert steps and steps[0]["status"] == "pass" and steps[0]["examples"] == ["total sales by city"]
        assert app.state.memory.size("store") == 2

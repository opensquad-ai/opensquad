"""MemoryStore fuzzy search / get_many / list_entry_ids batching."""

from __future__ import annotations

import time

from opensquad.tools.agent_memory_tool.memory.retriever import MemoryRetriever
from opensquad.tools.agent_memory_tool.memory.storage import MemoryStore


def test_fuzzy_hits_substring_not_exact():
    store = MemoryStore()
    war = store.add(keywords=["trade war"], summary="war")
    exact = store.add(keywords=["trade"], summary="exact")
    intl = store.add(keywords=["international trade"], summary="intl")

    fuzzy = store.search_fuzzy(["trade"])
    assert war in fuzzy
    assert intl in fuzzy
    assert exact not in fuzzy
    assert abs(fuzzy[war] - (len("trade") / len("trade war"))) < 1e-9
    assert abs(fuzzy[intl] - (len("trade") / len("international trade"))) < 1e-9

    exact_hits = store.search_exact(["trade"])
    assert exact in exact_hits
    assert war not in exact_hits


def test_fuzzy_index_word_inside_query():
    store = MemoryStore()
    eid = store.add(keywords=["trade"], summary="base")
    query = "international trade"
    fuzzy = store.search_fuzzy([query])
    assert eid in fuzzy
    assert abs(fuzzy[eid] - (len("trade") / len(query) * 0.8)) < 1e-9


def test_fuzzy_empty_and_percent_literal():
    store = MemoryStore()
    eid = store.add(keywords=["100% tariff"], summary="pct")
    assert store.search_fuzzy([]) == {}
    assert store.search_fuzzy([""]) == {}
    fuzzy = store.search_fuzzy(["100%"])
    assert eid in fuzzy


def test_get_many_and_list_entry_ids():
    store = MemoryStore()
    now = time.time()
    old = store.add(keywords=["old"], summary="old", source="a", timestamp=now - 7200)
    new = store.add(keywords=["new"], summary="new", source="b", timestamp=now)

    assert store.get_many([]) == {}
    assert store.get_many(["missing"]) == {}
    got = store.get_many([old, new, "missing"])
    assert set(got) == {old, new}
    assert got[old]["summary"] == "old"
    assert got[new]["source"] == "b"

    assert set(store.list_entry_ids(source_filter="a")) == {old}
    recent = store.list_entry_ids(time_recent=1)
    assert new in recent
    assert old not in recent
    ranged = store.list_entry_ids(time_range=(now - 100, now + 100))
    assert set(ranged) == {new}


def test_retriever_fast_source_filter_without_full_table():
    store = MemoryStore()
    keep = store.add(keywords=["trade war"], summary="keep", source="agent_a")
    store.add(keywords=["trade war"], summary="drop", source="agent_b")
    retriever = MemoryRetriever(store)
    result = retriever.retrieve(keywords=["trade"], depth="fast", source_filter="agent_a")
    ids = {m["entry_id"] for m in result["matched_entries"]}
    assert ids == {keep}
    assert result["search_stats"]["fuzzy_hits"] == 1

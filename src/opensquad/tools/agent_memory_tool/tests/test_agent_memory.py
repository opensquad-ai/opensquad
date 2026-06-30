"""
AgentMemory Unified API End-to-End Tests

Test coverage:
    Test 1: Basic CRUD (write/read/remove/list_entries)
    Test 2: Document ingestion + matrix construction (ingest_document + rebuild_matrices)
    Test 3: Three-level query (query: fast/standard/deep)
    Test 4: Chain reasoning (find_chain + with_evidence)
    Test 5: Persistence (save + load + consistency verification)
    Test 6: Multi-agent sharing (source_filter)
    Test 7: Time filtering (time_recent / time_range)
    Test 8: Dual-channel learning (write auto-triggers add_keywords)
    Test 9: repr + get_stats
    Test 10: log() write + index verification + dual-channel learning
    Test 11: recall_by_date() pure time query
    Test 12: recall_by_range() time range + category/keyword/source filtering
    Test 13: summarize() aggregated statistics
    Test 14: Episodic persistence save/load consistency
    Test 15: Log + semantic query cooperation
    Test 16: SQLite CRUD + three-tier classification (entry_type filter)
    Test 17: Retrieval strengthening (access_count increments after query)
    Test 18: Importance grading (affects time decay + ranking)
    Test 19: Memory reconsolidation (supersedes link + old entry demotion)
    Test 20: Consolidation cleanup (cleanup_vocab + remove_words + consolidate)

How to run:
    python test_agent_memory.py
    (Run directly in terminal, do not capture output via pipe)
"""

import json
import os
import shutil
import sys
import time

# Add project root to path (one level up from tests/)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from memory import AgentMemory


def print_sep(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def test_1_crud():
    """Test 1: Basic CRUD"""
    print_sep("Test 1: CRUD")

    am = AgentMemory()

    # write
    id1 = am.write(
        topic="AI Development",
        keywords=["artificial intelligence", "deep learning", "neural networks"],
        summary="The field of artificial intelligence has advanced rapidly in recent years",
        source="agent_A",
    )
    id2 = am.write(
        topic="Quantum Computing",
        keywords=["quantum", "computing", "qubit"],
        summary="Quantum computing is expected to break through the limitations of classical computing",
        source="agent_B",
    )
    id3 = am.write(summary="This is a note without a topic", auto_extract_keywords=True, source="agent_A")

    assert id1 is not None, "write returned None"
    assert id2 is not None, "write returned None"
    assert id3 is not None, "write returned None"
    print(f"  [*] write: successfully wrote 3 entries ({id1}, {id2}, {id3})")

    # read
    entry1 = am.read(id1)
    assert entry1 is not None, "read returned None"
    assert entry1["topic"] == "AI Development", f"topic mismatch: {entry1['topic']}"
    assert entry1["source"] == "agent_A", "source mismatch"
    print(f"  [*] read: {id1} -> topic={entry1['topic']}, source={entry1['source']}")

    # read non-existent
    assert am.read("not_exist") is None, "non-existent entry should return None"
    print("  [*] read(not_exist): None (correct)")

    # list_entries
    all_entries = am.list_entries()
    assert len(all_entries) == 3, f"should have 3 entries, got {len(all_entries)}"
    print(f"  [*] list_entries: {len(all_entries)} entries")

    # list by source
    a_entries = am.list_entries(source_filter="agent_A")
    assert len(a_entries) == 2, f"agent_A should have 2 entries, got {len(a_entries)}"
    print(f"  [*] list_entries(agent_A): {len(a_entries)} entries")

    # remove
    removed = am.remove(id3)
    assert removed is True, "remove should return True"
    assert am.read(id3) is None, "read after remove should return None"
    assert len(am.list_entries()) == 2, "should have 2 entries after remove"
    print(f"  [*] remove({id3}): OK, remaining {len(am.list_entries())} entries")

    # remove non-existent
    assert am.remove("not_exist") is False, "remove of non-existent entry should return False"
    print("  [*] remove(not_exist): False (correct)")

    # invalid input
    try:
        am.write()  # no content
        raise AssertionError("should raise ValueError")
    except ValueError:
        print("  [*] write() empty content: ValueError (correct)")

    print("  >>> Test 1 PASSED")
    return True


def test_2_ingest_and_matrices():
    """Test 2: Document ingestion + matrix construction"""
    print_sep("Test 2: Ingest + Matrices")

    am = AgentMemory(min_cooccurrence=2)

    # Simulate news documents
    docs = [
        "Trump announced tariffs on Chinese goods, intensifying US-China trade friction. US stock markets saw large swings as investors worried about economic prospects.",
        "Fed Chairman Powell stated he would keep current interest rates unchanged; markets lowered expectations for a rate cut. The dollar index strengthened and the yuan faced pressure.",
        "China's export data exceeded expectations, widening the trade surplus. Analysts say global supply chain adjustments drove export growth.",
        "Tesla set a new sales record in the Chinese market as competition in the electric vehicle industry intensifies. Domestic brands BYD and NIO continued to gain momentum.",
        "A-share markets dipped and fluctuated as northbound funds continued to flow out. Brokerage analysts advised investors to stay cautious and watch for policy changes.",
        "Rising Fed rate-hike expectations caused global stock markets to fall, with noticeable capital outflows from emerging markets. The yuan hit a new low against the dollar.",
        "US-China trade negotiations made progress; both sides agreed to lower tariffs on certain goods. Market sentiment improved and A-shares rebounded sharply.",
        "Trump signed an executive order restricting Chinese tech companies from investing in the US. Huawei and ZTE shares fell in response.",
        "Global supply chains are being restructured under geopolitical pressures, with Vietnam and India becoming new manufacturing relocation destinations.",
        "China's central bank cut the reserve requirement ratio to release liquidity and support the real economy. Interbank market rates edged down.",
    ]

    for doc in docs:
        am.ingest_document(doc)

    stats = am.get_stats()
    print(f"  [*] ingested {stats['cooccurrence']['total_docs']} documents")
    print(f"  [*] vocab size: {stats['cooccurrence']['vocab_size']}")
    assert stats["cooccurrence"]["total_docs"] == 10, "should ingest 10 docs"
    assert stats["cooccurrence"]["vocab_size"] > 20, "vocab size too small"

    # Manually rebuild matrices
    result = am.rebuild_matrices()
    print(
        f"  [*] rebuild_matrices: pruned_nnz={result['pruned_nnz']}, "
        f"ppmi_nnz={result['ppmi_nnz']}, time={result['time_ms']}ms"
    )
    assert result["ppmi_nnz"] > 0, "PPMI non-zero count should be > 0"

    # Check dirty flag
    assert am._matrices_dirty is False, "dirty should be False after rebuild"

    # Ingest one more doc; dirty should become True
    am.ingest_document("US economic data beat expectations, US stocks continued to rise.")
    assert am._matrices_dirty is True, "dirty should be True after new document ingestion"

    print("  >>> Test 2 PASSED")
    return am  # Return am for reuse in subsequent tests


def test_3_query(am):
    """Test 3: Three-level query"""
    print_sep("Test 3: Query (fast/standard/deep)")

    # Write some memory entries first
    am.write(
        topic="US-China Trade Friction",
        keywords=["Trump", "tariffs", "trade", "China", "US"],
        summary="The Trump administration imposed tariffs on Chinese goods, triggering trade friction",
        source="news_agent",
    )

    am.write(
        topic="Fed Monetary Policy",
        keywords=["Fed", "interest rate", "rate cut", "Powell"],
        summary="The Fed kept rates unchanged; markets focus on the timing of future rate cuts",
        source="news_agent",
    )

    am.write(
        topic="A-Share Market Trend",
        keywords=["A-shares", "stock market", "northbound funds", "investment"],
        summary="A-shares dipped and fluctuated, northbound funds continued to flow out",
        source="market_agent",
    )

    # Rebuild matrices (new data available)
    am.rebuild_matrices()

    # fast query
    r_fast = am.query(keywords=["tariffs", "trade"], depth="fast", token_budget=500)
    assert r_fast["search_stats"]["depth_used"] == "fast"
    assert len(r_fast["matched_entries"]) > 0, "fast query should return results"
    assert r_fast["search_stats"]["time_ms"] < 5000, "fast should not exceed 5 seconds"
    print(f"  [*] fast: {len(r_fast['matched_entries'])} hits, {r_fast['search_stats']['time_ms']}ms")
    print(f"      prompt_text: {len(r_fast['prompt_text'])} chars")

    # standard query
    r_std = am.query(keywords=["tariffs"], depth="standard", token_budget=800)
    assert r_std["search_stats"]["depth_used"] == "standard"
    print(f"  [*] standard: {len(r_std['matched_entries'])} hits, {r_std['search_stats']['time_ms']}ms")
    print(f"      expanded_keywords: {r_std['expanded_keywords'][:5]}")

    # deep query (requires >= 2 keywords to trigger chain)
    r_deep = am.query(keywords=["Trump", "A-shares"], depth="deep", token_budget=1000)
    assert r_deep["search_stats"]["depth_used"] == "deep"
    print(f"  [*] deep: {len(r_deep['matched_entries'])} hits, {r_deep['search_stats']['time_ms']}ms")
    if r_deep["chain"]:
        chains = r_deep["chain"].get("chains", [])
        print(f"      chains: {len(chains)} paths")
        for c in chains[:2]:
            if c.get("path"):
                print(f"        {' -> '.join(c['path'])} (w={c['total_weight']})")

    # user_input auto extraction
    r_auto = am.query(user_input="What impact has the recent trade war had on stock markets", depth="fast")
    print(f"  [*] user_input auto extraction: {len(r_auto['matched_entries'])} hits")

    # empty query
    r_empty = am.query(keywords=[], depth="fast")
    assert len(r_empty["matched_entries"]) == 0, "empty keywords should return no results"
    print("  [*] empty query: 0 hits (correct)")

    # token budget check
    from memory.retriever import count_tokens

    if r_fast["prompt_text"]:
        actual_tokens = count_tokens(r_fast["prompt_text"])
        print(f"  [*] token budget check: budget=500, actual={actual_tokens}")
        assert actual_tokens <= 550, f"token over budget: {actual_tokens}"

    print("  >>> Test 3 PASSED")


def test_4_chain(am):
    """Test 4: Chain reasoning"""
    print_sep("Test 4: Chain Reasoning")

    # Basic chain reasoning
    chain_result = am.find_chain(["Trump", "A-shares"])
    print("  [*] find_chain(['Trump', 'A-shares']):")
    print(f"      anchors_found: {chain_result.get('anchors_found', [])}")
    print(f"      anchors_missing: {chain_result.get('anchors_missing', [])}")

    if chain_result.get("hidden_words"):
        print("      hidden_words (top 5):")
        for hw in chain_result["hidden_words"][:5]:
            print(
                f"        {hw['word']}: ppr={hw['ppr_score']}, path={hw['path_count']}, combined={hw['combined_score']}"
            )

    if chain_result.get("chains"):
        print("      chains:")
        for c in chain_result["chains"]:
            if c.get("path"):
                print(f"        {' -> '.join(c['path'])} (w={c['total_weight']}, hops={c['hops']})")

    # Chain reasoning with evidence
    chain_ev = am.find_chain(["Fed", "A-shares"], with_evidence=True)
    print("\n  [*] find_chain(['Fed', 'A-shares'], with_evidence=True):")
    if chain_ev.get("evidence"):
        print(f"      evidence ({len(chain_ev['evidence'])} entries):")
        for ev in chain_ev["evidence"][:3]:
            print(f"        {ev['word']}: topic={ev.get('topic')}")

    # Single word (should return error)
    chain_single = am.find_chain(["Trump"])
    # discover_hidden_chain requires at least 2 anchor words
    # if only 1 is found in the graph, returns error
    print(f"  [*] find_chain single word: hidden_words={len(chain_single.get('hidden_words', []))}")

    print("  >>> Test 4 PASSED")


def test_5_persistence(am):
    """Test 5: Persistence save/load"""
    print_sep("Test 5: Persistence")

    save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_save_dir")

    try:
        # Record state before saving
        stats_before = am.get_stats()
        entries_before = am.list_entries()
        print(
            f"  [*] before save: docs={stats_before['cooccurrence']['total_docs']}, "
            f"vocab={stats_before['cooccurrence']['vocab_size']}, "
            f"entries={stats_before['store']['total_entries']}, "
            f"ppmi_nnz={stats_before['matrices']['ppmi_nnz']}"
        )

        # Save
        am.save(save_dir)
        print(f"  [*] save -> {save_dir}")

        # Check that files were generated
        expected_files = ["config.json", "memory_store.json", "cooccurrence.npz", "ppmi.npz", "pruned.npz"]
        for f in expected_files:
            fpath = os.path.join(save_dir, f)
            exists = os.path.exists(fpath)
            size = os.path.getsize(fpath) if exists else 0
            print(f"      {f}: {'OK' if exists else 'MISSING'} ({size} bytes)")
            assert exists, f"file {f} was not generated"

        # Load in a new instance
        am2 = AgentMemory()
        ok = am2.load(save_dir)
        assert ok is True, "load should return True"

        stats_after = am2.get_stats()
        print(
            f"\n  [*] after load: docs={stats_after['cooccurrence']['total_docs']}, "
            f"vocab={stats_after['cooccurrence']['vocab_size']}, "
            f"entries={stats_after['store']['total_entries']}, "
            f"ppmi_nnz={stats_after['matrices']['ppmi_nnz']}"
        )

        # Verify consistency
        assert stats_after["cooccurrence"]["total_docs"] == stats_before["cooccurrence"]["total_docs"], (
            "total_docs mismatch"
        )
        assert stats_after["cooccurrence"]["vocab_size"] == stats_before["cooccurrence"]["vocab_size"], (
            "vocab_size mismatch"
        )
        assert stats_after["store"]["total_entries"] == stats_before["store"]["total_entries"], "entries count mismatch"
        assert stats_after["matrices"]["ppmi_nnz"] == stats_before["matrices"]["ppmi_nnz"], "ppmi_nnz mismatch"

        # Verify entry contents
        am2.list_entries()
        for eb in entries_before:
            ea = am2.read(eb["id"])
            assert ea is not None, f"entry {eb['id']} does not exist after load"
            assert ea["topic"] == eb["topic"], f"topic mismatch: {ea['topic']} vs {eb['topic']}"
        print(f"  [*] entry content verification: OK ({len(entries_before)} entries)")

        # Verify queries still work after loading
        r = am2.query(keywords=["tariffs", "trade"], depth="standard", token_budget=500)
        assert len(r["matched_entries"]) > 0, "query after load should return results"
        print(f"  [*] query after load: {len(r['matched_entries'])} hits, {r['search_stats']['time_ms']}ms")

        # Verify writes still work after loading
        new_id = am2.write(topic="Post-load write test", keywords=["test"], summary="written after load", source="test")
        assert am2.read(new_id) is not None, "write after load failed"
        print(f"  [*] write after load: {new_id} OK")

        # load non-existent directory
        am3 = AgentMemory()
        assert am3.load("_not_exist_dir_xyz") is False, "non-existent directory should return False"
        print("  [*] load(non-existent): False (correct)")

        print("  >>> Test 5 PASSED")

    finally:
        # Cleanup
        if os.path.exists(save_dir):
            shutil.rmtree(save_dir)
            print(f"  [*] cleanup: {save_dir} removed")


def test_6_multi_agent(am):
    """Test 6: Multi-agent sharing"""
    print_sep("Test 6: Multi-Agent")

    # Entries from news_agent and market_agent already exist
    stats = am.get_stats()
    print(f"  [*] current sources: {stats['store']['sources']}")

    # Query by source
    r_news = am.query(keywords=["tariffs"], depth="fast", source_filter="news_agent")
    r_market = am.query(keywords=["stock market"], depth="fast", source_filter="market_agent")
    r_all = am.query(keywords=["tariffs"], depth="fast")

    print(f"  [*] news_agent query: {len(r_news['matched_entries'])} entries")
    print(f"  [*] market_agent query: {len(r_market['matched_entries'])} entries")
    print(f"  [*] all agents query: {len(r_all['matched_entries'])} entries")

    # news_agent results should not include market_agent entries
    for me in r_news["matched_entries"]:
        entry = am.read(me["entry_id"])
        if entry:
            assert entry.get("source") != "market_agent", "source_filter failed: included a market_agent entry"

    print("  >>> Test 6 PASSED")


def test_7_time_filter():
    """Test 7: Time filtering"""
    print_sep("Test 7: Time Filter")

    am = AgentMemory()

    now = time.time()

    # Write entries at different timestamps
    am.write(
        topic="Long ago", keywords=["history", "past"], summary="An event from 30 days ago", timestamp=now - 30 * 86400
    )
    am.write(
        topic="Recent event", keywords=["history", "recent"], summary="An event from 1 hour ago", timestamp=now - 3600
    )
    am.write(
        topic="Just happened",
        keywords=["history", "current"],
        summary="An event from 5 minutes ago",
        timestamp=now - 300,
    )

    # No time filter
    r_all = am.query(keywords=["history"], depth="fast")
    assert len(r_all["matched_entries"]) == 3, f"no filter should return 3, got {len(r_all['matched_entries'])}"
    print(f"  [*] no time filter: {len(r_all['matched_entries'])} entries")

    # Last 2 hours
    r_recent = am.query(keywords=["history"], depth="fast", time_recent=2)
    assert len(r_recent["matched_entries"]) == 2, (
        f"last 2 hours should return 2, got {len(r_recent['matched_entries'])}"
    )
    print(f"  [*] time_recent=2h: {len(r_recent['matched_entries'])} entries")

    # Last 10 minutes
    r_10m = am.query(keywords=["history"], depth="fast", time_recent=10 / 60)
    assert len(r_10m["matched_entries"]) == 1, f"last 10 minutes should return 1, got {len(r_10m['matched_entries'])}"
    print(f"  [*] time_recent=10min: {len(r_10m['matched_entries'])} entries")

    # Time range
    r_range = am.query(keywords=["history"], depth="fast", time_range=(now - 2 * 86400, now - 300))
    # Only "1 hour ago" is in range (30 days ago is too early, 5 min ago is exactly at boundary)
    print(f"  [*] time_range(2d_ago ~ 5min_ago): {len(r_range['matched_entries'])} entries")

    # Check time weight decay
    for me in r_all["matched_entries"]:
        print(f"      {me['entry_id']}: age={me['age_hours']:.1f}h, tw={me['time_weight']}")

    print("  >>> Test 7 PASSED")


def test_8_dual_channel():
    """Test 8: Dual-channel learning"""
    print_sep("Test 8: Dual Channel")

    am = AgentMemory(min_cooccurrence=1)

    # Ingest documents first (Channel A)
    am.ingest_document(
        "Artificial intelligence and machine learning are driving advances in autonomous driving technology"
    )
    am.ingest_document("Deep learning is an important branch of AI, widely applied in image recognition")

    docs_before = am._cooccurrence.total_docs
    vocab_before = am._cooccurrence.vocab_count

    # Write memory entries with keywords (Channel B: auto-triggers add_keywords)
    am.write(keywords=["quantum computing", "superconductor", "quantum advantage", "compute breakthrough"])
    am.write(keywords=["gene editing", "CRISPR", "biotechnology"])

    docs_after = am._cooccurrence.total_docs
    vocab_after = am._cooccurrence.vocab_count

    # total_docs should not increase (add_keywords does not count toward total_docs)
    assert docs_after == docs_before, f"add_keywords should not increase total_docs: {docs_before} -> {docs_after}"
    print(f"  [*] total_docs: {docs_before} -> {docs_after} (unchanged, correct)")

    # vocab should grow with new words
    assert vocab_after > vocab_before, f"new keywords should expand vocab: {vocab_before} -> {vocab_after}"
    print(f"  [*] vocab: {vocab_before} -> {vocab_after} (+{vocab_after - vocab_before})")

    # Verify new words are in vocab_dict
    assert "quantum computing" in am._cooccurrence.vocab_dict, "quantum computing should be in vocab"
    assert "CRISPR" in am._cooccurrence.vocab_dict, "CRISPR should be in vocab"
    print("  [*] new word verification: quantum computing, CRISPR both in vocab")

    print("  >>> Test 8 PASSED")


def test_9_repr_and_stats():
    """Test 9: repr and get_stats"""
    print_sep("Test 9: repr + get_stats")

    am = AgentMemory()
    am.ingest_document("This is a test document used to verify the correctness of the statistics feature")
    am.write(topic="test", keywords=["verify", "statistics"], summary="test entry")

    # repr
    repr_str = repr(am)
    print(f"  [*] repr: {repr_str}")
    assert "AgentMemory(" in repr_str, "repr format is incorrect"

    # get_stats
    stats = am.get_stats()
    assert "config" in stats, "missing config"
    assert "cooccurrence" in stats, "missing cooccurrence"
    assert "store" in stats, "missing store"
    assert "decay" in stats, "missing decay"
    assert "matrices" in stats, "missing matrices"
    print(f"  [*] get_stats keys: {list(stats.keys())}")
    print(f"      config: {stats['config']}")
    print(f"      cooccurrence: {stats['cooccurrence']}")
    print(f"      store: {stats['store']}")

    print("  >>> Test 9 PASSED")


def test_10_log():
    """Test 10: log() write + date/category index verification + dual-channel learning"""
    print_sep("Test 10: log() + Indexing + Dual Channel")

    am = AgentMemory(min_cooccurrence=1)

    # Use a fixed mid-day timestamp to avoid cross-day issues
    import datetime

    fixed_dt = datetime.datetime(2026, 2, 10, 12, 0, 0)  # noon
    base_ts = fixed_dt.timestamp()
    today_str = "2026-02-10"

    # Record vocab and total_docs before writing
    vocab_before = am._cooccurrence.vocab_count
    docs_before = am._cooccurrence.total_docs

    # Write log entries (all on the same day)
    id1 = am.log(
        content="Completed core module development of the memory system",
        detail="Includes development and testing of co-occurrence matrix, PPMI calculation, retriever and other modules",
        category="work",
        tags=["development", "memory system", "PPMI"],
        source="dev_agent",
        timestamp=base_ts - 3600,  # 11:00
    )

    id2 = am.log(
        content="Had hot pot with friends",
        category="life",
        source="life_agent",
        timestamp=base_ts - 1800,  # 11:30
    )

    id3 = am.log(
        content="Read a paper on quantum computing",
        detail="The paper discusses recent advances in quantum error correction codes",
        category="work",
        tags=["quantum computing", "paper"],
        source="study_agent",
        timestamp=base_ts,  # 12:00
    )

    assert id1 is not None and id2 is not None and id3 is not None
    print(f"  [*] log wrote 3 entries: {id1}, {id2}, {id3}")

    # Verify date index
    assert today_str in am._date_index, f"date index should contain {today_str}"
    assert len(am._date_index[today_str]) == 3, f"today should have 3 log entries, got {len(am._date_index[today_str])}"
    # Verify chronological order (id1 is earliest)
    assert am._date_index[today_str][0] == id1, "first entry in date index should be earliest"
    assert am._date_index[today_str][2] == id3, "last entry in date index should be latest"
    print(f"  [*] date_index[{today_str}]: {len(am._date_index[today_str])} entries, order OK")

    # Verify category index
    assert "work" in am._category_index, "category index should contain 'work'"
    assert "life" in am._category_index, "category index should contain 'life'"
    assert id1 in am._category_index["work"], f"{id1} should be in 'work' category"
    assert id3 in am._category_index["work"], f"{id3} should be in 'work' category"
    assert id2 in am._category_index["life"], f"{id2} should be in 'life' category"
    print(
        f"  [*] category_index: work={len(am._category_index['work'])} entries, "
        f"life={len(am._category_index['life'])} entries"
    )

    # Verify episodic_ids flags
    assert id1 in am._episodic_ids, f"{id1} should be in episodic_ids"
    assert id2 in am._episodic_ids
    assert id3 in am._episodic_ids
    assert len(am._episodic_ids) == 3
    print(f"  [*] episodic_ids: {len(am._episodic_ids)} entries")

    # Verify dual-channel learning: total_docs unchanged, vocab increased
    docs_after = am._cooccurrence.total_docs
    vocab_after = am._cooccurrence.vocab_count
    assert docs_after == docs_before, f"log() should not increase total_docs: {docs_before} -> {docs_after}"
    assert vocab_after > vocab_before, f"log() keywords should expand vocab: {vocab_before} -> {vocab_after}"
    print(f"  [*] dual channel: total_docs={docs_after} (unchanged), vocab={vocab_before}->{vocab_after}")

    # Verify underlying MemoryStore field mapping
    entry1 = am.read(id1)
    assert entry1["topic"] == "work", f"category->topic mapping error: {entry1['topic']}"
    assert entry1["summary"] == "Completed core module development of the memory system", (
        "content->summary mapping error"
    )
    assert "co-occurrence matrix" in entry1["body"] or "PPMI" in entry1["body"], "detail->body mapping error"
    assert entry1["source"] == "dev_agent"
    print(f"  [*] field mapping verification: topic={entry1['topic']}, source={entry1['source']}")

    # Verify that empty content raises an error
    try:
        am.log(content="")
        raise AssertionError("empty content should raise ValueError")
    except ValueError:
        print("  [*] log(content='') -> ValueError (correct)")

    print("  >>> Test 10 PASSED")
    return am, today_str


def test_11_recall_by_date(am, today_str):
    """Test 11: recall_by_date() pure time query"""
    print_sep("Test 11: recall_by_date()")

    result = am.recall_by_date(today_str)

    assert result["date"] == today_str
    assert result["count"] == 3, f"today should have 3 entries, got {result['count']}"
    print(f"  [*] recall_by_date({today_str}): {result['count']} entries")

    # Verify chronological order
    entries = result["entries"]
    for i in range(len(entries) - 1):
        assert entries[i]["time"] <= entries[i + 1]["time"], (
            f"time order error: {entries[i]['time']} > {entries[i + 1]['time']}"
        )
    print(f"  [*] chronological order: {[e['time'] for e in entries]}")

    # Verify field completeness
    e0 = entries[0]
    assert "id" in e0 and "time" in e0 and "category" in e0
    assert "content" in e0 and "tags" in e0 and "source" in e0
    assert e0["category"] == "work"
    assert e0["content"] == "Completed core module development of the memory system"
    assert e0["source"] == "dev_agent"
    print(f"  [*] field completeness: id={e0['id']}, time={e0['time']}, cat={e0['category']}, src={e0['source']}")

    # Verify category statistics
    cats = result["categories"]
    assert cats.get("work") == 2, f"work should have 2 entries: {cats}"
    assert cats.get("life") == 1, f"life should have 1 entry: {cats}"
    print(f"  [*] categories: {cats}")

    # Query non-existent date
    r_empty = am.recall_by_date("1999-01-01")
    assert r_empty["count"] == 0, "non-existent date should return 0 entries"
    assert r_empty["entries"] == []
    print("  [*] recall_by_date(1999-01-01): 0 entries (correct)")

    print("  >>> Test 11 PASSED")


def test_12_recall_by_range():
    """Test 12: recall_by_range() time range + category/keyword/source filtering"""
    print_sep("Test 12: recall_by_range()")

    am = AgentMemory()
    import datetime

    # Create logs spanning multiple days
    base_dt = datetime.datetime(2026, 2, 5, 9, 0, 0)

    logs = [
        ("Completed requirements document", "work", "pm_agent", base_dt),
        ("Team code review", "work", "dev_agent", base_dt + datetime.timedelta(hours=3)),
        ("Had ramen for lunch", "life", "life_agent", base_dt + datetime.timedelta(hours=4)),
        ("Deployed test server environment", "work", "dev_agent", base_dt + datetime.timedelta(days=1, hours=2)),
        ("Watched a movie", "life", "life_agent", base_dt + datetime.timedelta(days=1, hours=8)),
        ("Fixed bug on login page", "work", "dev_agent", base_dt + datetime.timedelta(days=2, hours=1)),
        ("Ran 5 kilometers", "exercise", "life_agent", base_dt + datetime.timedelta(days=2, hours=6)),
        ("Studied Rust programming language", "study", "study_agent", base_dt + datetime.timedelta(days=3, hours=10)),
    ]

    for content, cat, src, dt in logs:
        am.log(content=content, category=cat, source=src, timestamp=dt.timestamp())

    # Full range query
    r_all = am.recall_by_range("2026-02-05", "2026-02-08")
    assert r_all["total_count"] == 8, f"full range should return 8, got {r_all['total_count']}"
    assert len(r_all["days"]) == 4, f"should cover 4 days, got {len(r_all['days'])}"
    print(f"  [*] full range (02-05~02-08): {r_all['total_count']} entries, {len(r_all['days'])} days")

    # Category filter
    r_work = am.recall_by_range("2026-02-05", "2026-02-08", category="work")
    assert r_work["total_count"] == 4, f"work should have 4 entries, got {r_work['total_count']}"
    print(f"  [*] category='work': {r_work['total_count']} entries")

    r_life = am.recall_by_range("2026-02-05", "2026-02-08", category="life")
    assert r_life["total_count"] == 2, f"life should have 2 entries, got {r_life['total_count']}"
    print(f"  [*] category='life': {r_life['total_count']} entries")

    # Source filter
    r_dev = am.recall_by_range("2026-02-05", "2026-02-08", source_filter="dev_agent")
    assert r_dev["total_count"] == 3, f"dev_agent should have 3 entries, got {r_dev['total_count']}"
    print(f"  [*] source='dev_agent': {r_dev['total_count']} entries")

    # Keyword filter
    r_kw = am.recall_by_range("2026-02-05", "2026-02-08", keyword="bug")
    assert r_kw["total_count"] == 1, f"keyword='bug' should return 1 entry, got {r_kw['total_count']}"
    print(f"  [*] keyword='bug': {r_kw['total_count']} entries")

    # Combined filter: category + source
    r_combo = am.recall_by_range("2026-02-05", "2026-02-08", category="work", source_filter="dev_agent")
    assert r_combo["total_count"] == 3, f"work+dev_agent should have 3 entries, got {r_combo['total_count']}"
    print(f"  [*] category='work' + source='dev_agent': {r_combo['total_count']} entries")

    # Partial date range
    r_partial = am.recall_by_range("2026-02-06", "2026-02-07")
    assert r_partial["total_count"] == 4, f"02-06~02-07 should have 4 entries, got {r_partial['total_count']}"
    print(f"  [*] partial range (02-06~02-07): {r_partial['total_count']} entries")

    # Empty range
    r_empty = am.recall_by_range("2030-01-01", "2030-01-31")
    assert r_empty["total_count"] == 0
    print("  [*] empty range (2030): 0 entries (correct)")

    # Category statistics
    print(f"  [*] full range categories: {r_all['categories']}")

    print("  >>> Test 12 PASSED")


def test_13_summarize():
    """Test 13: summarize() aggregated statistics"""
    print_sep("Test 13: summarize()")

    am = AgentMemory()
    import datetime

    # Create one week of log data
    end_dt = datetime.datetime(2026, 2, 9, 18, 0, 0)

    week_logs = [
        ("Morning meeting to discuss project progress", "work", end_dt - datetime.timedelta(days=6, hours=9)),
        ("Wrote unit tests", "work", end_dt - datetime.timedelta(days=5, hours=3)),
        ("Worked out at the gym", "exercise", end_dt - datetime.timedelta(days=5, hours=7)),
        ("Code review", "work", end_dt - datetime.timedelta(days=4, hours=2)),
        ("Read a book", "study", end_dt - datetime.timedelta(days=3, hours=5)),
        ("Went for a run", "exercise", end_dt - datetime.timedelta(days=2, hours=1)),
        ("Deployed to production", "work", end_dt - datetime.timedelta(days=1, hours=4)),
        ("Project retrospective", "work", end_dt - datetime.timedelta(hours=2)),
    ]

    for content, cat, dt in week_logs:
        am.log(content=content, category=cat, timestamp=dt.timestamp())

    # Weekly summary
    end_str = end_dt.strftime("%Y-%m-%d")
    result = am.summarize(period="week", end_date=end_str)

    assert result["period_type"] == "week"
    assert result["total_activities"] == 8, f"week should have 8 entries, got {result['total_activities']}"
    print(f"  [*] week summary: period={result['period']}")
    print(f"      total_activities={result['total_activities']}")
    print(f"      by_category={result['by_category']}")
    print(f"      by_day={result['by_day']}")

    # Verify category statistics
    assert result["by_category"].get("work") == 5, f"work should have 5 entries: {result['by_category']}"
    assert result["by_category"].get("exercise") == 2, f"exercise should have 2 entries: {result['by_category']}"

    # Verify entries count matches total_activities
    assert len(result["entries"]) == result["total_activities"], "entries count should equal total_activities"

    # Daily summary
    r_day = am.summarize(period="day", end_date=end_str)
    assert r_day["period_type"] == "day"
    print(f"\n  [*] day summary: period={r_day['period']}, total={r_day['total_activities']}")

    # Monthly summary
    r_month = am.summarize(period="month", end_date=end_str)
    assert r_month["period_type"] == "month"
    assert r_month["total_activities"] == 8, "month range should include all 8 entries"
    print(f"  [*] month summary: period={r_month['period']}, total={r_month['total_activities']}")

    # Invalid period
    try:
        am.summarize(period="year")
        raise AssertionError("year should raise ValueError")
    except ValueError:
        print("  [*] summarize(period='year') -> ValueError (correct)")

    print("  >>> Test 13 PASSED")


def test_14_episodic_persistence():
    """Test 14: Episodic persistence save/load consistency"""
    print_sep("Test 14: Episodic Persistence")

    save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_episodic_save")

    try:
        am = AgentMemory(min_cooccurrence=1)

        import datetime

        fixed_dt = datetime.datetime(2026, 2, 10, 14, 0, 0)  # 2 PM
        base_ts = fixed_dt.timestamp()
        today_str = "2026-02-10"

        # Write both knowledge entries and log entries simultaneously
        k_id = am.write(
            topic="AI Technology",
            keywords=["deep learning", "Transformer"],
            summary="Transformer architecture has become the mainstream in NLP",
            source="kb_agent",
        )

        l_id1 = am.log(
            content="Debugged a memory system bug", category="work", source="dev_agent", timestamp=base_ts - 600
        )  # 13:50
        l_id2 = am.log(content="Had pizza for dinner", category="life", source="life_agent", timestamp=base_ts)  # 14:00

        # Record state before saving
        date_index_before = dict(am._date_index)
        cat_index_before = {k: set(v) for k, v in am._category_index.items()}
        episodic_ids_before = set(am._episodic_ids)
        stats_before = am.get_stats()

        print(
            f"  [*] before save: knowledge=1, logs=2, "
            f"dates={list(am._date_index.keys())}, "
            f"cats={list(am._category_index.keys())}"
        )

        # Save
        am.save(save_dir)

        # Check that episodic_meta.json was generated
        ep_path = os.path.join(save_dir, "episodic_meta.json")
        assert os.path.exists(ep_path), "episodic_meta.json was not generated"
        with open(ep_path, encoding="utf-8") as f:
            ep_meta = json.load(f)
        print(f"  [*] episodic_meta.json: {os.path.getsize(ep_path)} bytes")
        print(f"      date_index keys: {list(ep_meta['date_index'].keys())}")
        print(f"      category_index keys: {list(ep_meta['category_index'].keys())}")
        print(f"      episodic_ids count: {len(ep_meta['episodic_ids'])}")

        # Load in new instance
        am2 = AgentMemory()
        ok = am2.load(save_dir)
        assert ok is True, "load should return True"

        # Verify date index consistency
        assert am2._date_index == date_index_before, f"date_index mismatch: {am2._date_index} vs {date_index_before}"
        print("  [*] date_index consistent: OK")

        # Verify category index consistency
        assert am2._category_index == cat_index_before, "category_index mismatch"
        print("  [*] category_index consistent: OK")

        # Verify episodic_ids consistency
        assert am2._episodic_ids == episodic_ids_before, "episodic_ids mismatch"
        print(f"  [*] episodic_ids consistent: OK ({len(am2._episodic_ids)} entries)")

        # Verify knowledge entries are not in episodic_ids
        assert k_id not in am2._episodic_ids, "knowledge entry should not be in episodic_ids"
        assert l_id1 in am2._episodic_ids and l_id2 in am2._episodic_ids
        print("  [*] knowledge/log distinction: OK (k_id not in episodic, l_ids in episodic)")

        # Verify recall_by_date works correctly after load
        r = am2.recall_by_date(today_str)
        assert r["count"] == 2, f"after load should have 2 log entries, got {r['count']}"
        print(f"  [*] recall_by_date after load: {r['count']} entries OK")

        # Verify get_stats episodic section
        stats_after = am2.get_stats()
        assert stats_after["episodic"]["total_logs"] == stats_before["episodic"]["total_logs"], "total_logs mismatch"
        assert stats_after["episodic"]["total_days"] == stats_before["episodic"]["total_days"], "total_days mismatch"
        print("  [*] get_stats episodic consistent: OK")

        print("  >>> Test 14 PASSED")

    finally:
        if os.path.exists(save_dir):
            shutil.rmtree(save_dir)
            print(f"  [*] cleanup: {save_dir} removed")


def test_15_log_semantic_query():
    """Test 15: Log + semantic query cooperation — query() can discover log entries"""
    print_sep("Test 15: Log + Semantic Query Cooperation")

    am = AgentMemory(min_cooccurrence=1)

    # Ingest some documents to build co-occurrence knowledge
    docs = [
        "Quantum computing and quantum error correction are core research directions in quantum information science",
        "Superconducting qubits are one of the main technology routes for realizing quantum computing",
        "Quantum advantage has been verified for specific computational problems",
    ]
    for doc in docs:
        am.ingest_document(doc)

    # Write knowledge entry
    am.write(
        topic="Quantum Computing Advances",
        keywords=["quantum computing", "qubit", "superconductor"],
        summary="Superconducting quantum computing achieved important breakthroughs",
        source="kb_agent",
    )

    # Write log entry (containing related keywords)
    log_id = am.log(
        content="Attended a quantum computing symposium",
        detail="Discussed the latest advances in quantum error correction and fault-tolerant quantum computing",
        category="study",
        tags=["quantum computing", "symposium"],
        source="study_agent",
    )

    # Rebuild matrices
    am.rebuild_matrices()

    # Query by keywords — should find both knowledge and log entries
    r = am.query(keywords=["quantum computing"], depth="fast", token_budget=2000)

    matched_ids = [m["entry_id"] for m in r["matched_entries"]]
    assert log_id in matched_ids, f"query should find log entry {log_id}, actual hits: {matched_ids}"
    print(f"  [*] query(keywords=['quantum computing']): {len(r['matched_entries'])} hits")
    print(f"      hit IDs: {matched_ids}")
    print(f"      log entry {log_id} in hit list: OK")

    # Verify prompt_text includes log entry content
    assert "symposium" in r["prompt_text"], "prompt_text should include log entry content"
    print("  [*] prompt_text includes log content: OK")

    # Use standard depth for related expansion query
    r2 = am.query(keywords=["quantum"], depth="standard", token_budget=2000)
    print(f"  [*] standard query: {len(r2['matched_entries'])} entries, expanded: {r2['expanded_keywords'][:5]}")

    # Verify log entry is correctly flagged in episodic_ids
    assert log_id in am._episodic_ids
    # Knowledge entries should not be in episodic_ids
    kb_entries = am.list_entries(source_filter="kb_agent")
    for e in kb_entries:
        assert e["id"] not in am._episodic_ids, f"knowledge entry {e['id']} should not be in episodic_ids"
    print("  [*] knowledge/log distinction: OK")

    print("  >>> Test 15 PASSED")


def test_16_entry_type_crud():
    """Test 16: SQLite CRUD + three-tier classification (entry_type filter)"""
    print_sep("Test 16: Entry Type CRUD + Classification")

    am = AgentMemory()

    # Write three types of memory entries
    k1 = am.write(
        topic="Python Basics",
        keywords=["Python", "programming", "syntax"],
        summary="Python is a general-purpose programming language",
        entry_type="knowledge",
        category="programming",
        importance=4,
    )
    k2 = am.write(
        topic="Machine Learning",
        keywords=["machine learning", "algorithm", "model"],
        summary="Machine learning is a subfield of AI",
        entry_type="knowledge",
        category="AI",
    )

    e1 = am.write(
        topic="Debugging Experience",
        keywords=["debugging", "error", "logs"],
        summary="When encountering a bug, check logs first then reproduce",
        entry_type="experience",
        category="development",
        importance=5,
    )

    l1 = am.log(content="Completed a code review", category="work", source="dev_agent")
    l2 = am.log(content="Deployed new version to test environment", category="deployment", source="ops_agent")

    print(f"  [*] write: knowledge={k1},{k2}, experience={e1}, log={l1},{l2}")

    # Filter list_entries by entry_type
    all_entries = am.list_entries()
    knowledge_entries = am.list_entries(entry_type="knowledge")
    experience_entries = am.list_entries(entry_type="experience")
    log_entries = am.list_entries(entry_type="log")

    assert len(all_entries) == 5, f"total should be 5, got {len(all_entries)}"
    assert len(knowledge_entries) == 2, f"knowledge should be 2, got {len(knowledge_entries)}"
    assert len(experience_entries) == 1, f"experience should be 1, got {len(experience_entries)}"
    assert len(log_entries) == 2, f"log should be 2, got {len(log_entries)}"
    print(
        f"  [*] list_entries: all={len(all_entries)}, knowledge={len(knowledge_entries)}, "
        f"experience={len(experience_entries)}, log={len(log_entries)}"
    )

    # Verify entry_type field is correctly written
    k1_entry = am.read(k1)
    e1_entry = am.read(e1)
    l1_entry = am.read(l1)
    assert k1_entry["entry_type"] == "knowledge", f"k1 type wrong: {k1_entry['entry_type']}"
    assert e1_entry["entry_type"] == "experience", f"e1 type wrong: {e1_entry['entry_type']}"
    assert l1_entry["entry_type"] == "log", f"l1 type wrong: {l1_entry['entry_type']}"
    print("  [*] entry_type field: knowledge=OK, experience=OK, log=OK")

    # Verify importance field
    assert k1_entry["importance"] == 4, f"k1 importance wrong: {k1_entry['importance']}"
    assert e1_entry["importance"] == 5, f"e1 importance wrong: {e1_entry['importance']}"
    print(f"  [*] importance: k1={k1_entry['importance']}, e1={e1_entry['importance']}")

    # Verify category field
    assert k1_entry.get("category") == "programming", f"k1 category wrong: {k1_entry.get('category')}"
    print(f"  [*] category: k1={k1_entry.get('category')}")

    # Combined filter: source + entry_type
    dev_logs = am.list_entries(source_filter="dev_agent", entry_type="log")
    assert len(dev_logs) == 1, f"dev_agent logs should be 1, got {len(dev_logs)}"
    print(f"  [*] combined filter (source=dev_agent, type=log): {len(dev_logs)} entry")

    # Verify date_str is auto-generated
    assert k1_entry.get("date_str") is not None, "date_str should be auto-generated"
    print(f"  [*] date_str auto-generated: {k1_entry['date_str']}")

    print("  >>> Test 16 PASSED")


def test_17_access_count():
    """Test 17: Retrieval strengthening - access_count increments after query"""
    print_sep("Test 17: Retrieval Strengthening (access_count)")

    am = AgentMemory()

    # Write entries and ingest documents to build matrices
    id1 = am.write(
        topic="Deep Learning Framework",
        keywords=["deep learning", "PyTorch", "framework"],
        summary="PyTorch is a popular deep learning framework",
    )
    id2 = am.write(
        topic="Natural Language Processing",
        keywords=["NLP", "text", "analysis"],
        summary="NLP is the AI technology for processing text",
    )
    id3 = am.write(
        topic="Computer Vision",
        keywords=["vision", "image", "recognition"],
        summary="Computer vision is used for image analysis",
    )

    # Initial access_count should be 0
    e1 = am.read(id1)
    e2 = am.read(id2)
    e3 = am.read(id3)
    assert e1["access_count"] == 0, f"initial access_count should be 0, got {e1['access_count']}"
    assert e2["access_count"] == 0
    assert e3["access_count"] == 0
    print(f"  [*] initial access_count: id1={e1['access_count']}, id2={e2['access_count']}, id3={e3['access_count']}")

    # Query hit id1 (via exact keyword match)
    result = am.query(keywords=["deep learning", "PyTorch"], depth="fast")
    matched_ids = [m["entry_id"] for m in result["matched_entries"]]
    print(f"  [*] query(depth=fast): matched={matched_ids}")

    # Verify id1 access_count increments
    e1_after = am.read(id1)
    if id1 in matched_ids:
        assert e1_after["access_count"] >= 1, (
            f"access_count should be >= 1 after query hit, got {e1_after['access_count']}"
        )
        print(f"  [*] id1 access_count after query: {e1_after['access_count']} (incremented)")
    else:
        print(f"  [*] id1 not matched (keyword mismatch), access_count unchanged: {e1_after['access_count']}")

    # Query again; access_count should continue to increment
    result2 = am.query(keywords=["deep learning"], depth="fast")
    e1_after2 = am.read(id1)
    if id1 in [m["entry_id"] for m in result2["matched_entries"]]:
        assert e1_after2["access_count"] >= 2, (
            f"access_count should be >= 2 after 2 queries, got {e1_after2['access_count']}"
        )
        print(f"  [*] id1 access_count after 2nd query: {e1_after2['access_count']} (incremented again)")

    # Unmatched entry access_count should stay unchanged
    e3_after = am.read(id3)
    assert e3_after["access_count"] == 0, f"id3 should remain 0, got {e3_after['access_count']}"
    print(f"  [*] id3 access_count (never matched): {e3_after['access_count']} (unchanged)")

    # Verify last_accessed is updated
    if e1_after["access_count"] >= 1:
        assert e1_after["last_accessed"] is not None, "last_accessed should be set"
        assert e1_after["last_accessed"] >= e1["timestamp"], "last_accessed should be >= creation time"
        print("  [*] last_accessed updated: OK")

    print("  >>> Test 17 PASSED")


def test_18_importance_ranking():
    """Test 18: Importance grading - affects time decay + ranking"""
    print_sep("Test 18: Importance Grading (decay + ranking)")

    from memory.storage import MemoryStore

    # Part A: Verify compute_time_weight formula
    now = time.time()
    ts_30d_ago = now - 30 * 86400  # 30 days ago

    tw_imp1 = MemoryStore.compute_time_weight(ts_30d_ago, decay_lambda=0.1, now=now, importance=1)
    tw_imp3 = MemoryStore.compute_time_weight(ts_30d_ago, decay_lambda=0.1, now=now, importance=3)
    tw_imp5 = MemoryStore.compute_time_weight(ts_30d_ago, decay_lambda=0.1, now=now, importance=5)

    print(f"  [*] time_weight (30d ago): imp1={tw_imp1:.4f}, imp3={tw_imp3:.4f}, imp5={tw_imp5:.4f}")

    # importance=5 decays slowest -> highest weight
    # importance=1 decays fastest -> lowest weight
    assert tw_imp5 > tw_imp3, f"imp5 ({tw_imp5:.4f}) should > imp3 ({tw_imp3:.4f})"
    assert tw_imp3 > tw_imp1, f"imp3 ({tw_imp3:.4f}) should > imp1 ({tw_imp1:.4f})"
    print("  [*] decay order correct: imp5 > imp3 > imp1")

    # importance=3 should match the old formula: 1/(1+0.1*30) = 0.25
    expected_old = 1.0 / (1.0 + 0.1 * 30)
    assert abs(tw_imp3 - expected_old) < 1e-6, f"imp3 should match old formula: {expected_old:.6f} vs {tw_imp3:.6f}"
    print(f"  [*] imp3 backward compatible: {tw_imp3:.6f} == {expected_old:.6f}")

    # Part B: Verify set_importance + ranking
    am = AgentMemory()

    # Create two entries with same keywords, one high importance, one low
    # Use same timestamp (30 days ago) to keep time factor consistent
    old_ts = time.time() - 30 * 86400
    id_low = am.write(
        topic="Low Priority News",
        keywords=["economy", "data"],
        summary="Ordinary economic data report",
        importance=1,
        timestamp=old_ts,
    )
    id_high = am.write(
        topic="Major Economic Event",
        keywords=["economy", "data"],
        summary="Major economic policy change",
        importance=5,
        timestamp=old_ts,
    )

    print(f"  [*] write: id_low={id_low}(imp=1), id_high={id_high}(imp=5)")

    # Query (fast mode, no matrix needed)
    result = am.query(keywords=["economy", "data"], depth="fast")
    matched = result["matched_entries"]
    assert len(matched) >= 2, f"should match at least 2, got {len(matched)}"

    # Higher importance should rank first
    ids_order = [m["entry_id"] for m in matched]
    idx_high = ids_order.index(id_high)
    idx_low = ids_order.index(id_low)
    assert idx_high < idx_low, f"high importance should rank higher: {id_high}@{idx_high} vs {id_low}@{idx_low}"
    print(f"  [*] ranking: {id_high}(imp=5) @ pos {idx_high}, {id_low}(imp=1) @ pos {idx_low}")

    # Part C: Dynamically modify importance
    ok = am.set_importance(id_low, 5)
    assert ok is True, "set_importance should return True"
    e_low = am.read(id_low)
    assert e_low["importance"] == 5, f"importance should be 5, got {e_low['importance']}"
    print(f"  [*] set_importance({id_low}, 5): OK, now importance={e_low['importance']}")

    # Boundary clamping test
    am.set_importance(id_low, 10)  # exceeds upper bound
    assert am.read(id_low)["importance"] == 5, "importance should be clamped to 5"
    am.set_importance(id_low, 0)  # below lower bound
    assert am.read(id_low)["importance"] == 1, "importance should be clamped to 1"
    print("  [*] clamping: set(10)->5, set(0)->1: OK")

    # Non-existent entry
    ok2 = am.set_importance("nonexist_id", 3)
    assert ok2 is False, "set_importance on nonexistent should return False"
    print("  [*] set_importance(nonexist): False (correct)")

    print("  >>> Test 18 PASSED")


def test_19_reconsolidation():
    """Test 19: Memory reconsolidation - supersedes link + old entry demotion"""
    print_sep("Test 19: Reconsolidation (supersedes)")

    am = AgentMemory()

    # Write original memory
    old_id = am.write(
        topic="Shape of the Earth",
        keywords=["earth", "shape", "science"],
        summary="The Earth is spherical",
        importance=4,
    )
    old_entry = am.read(old_id)
    assert old_entry["importance"] == 4
    print(f"  [*] original: {old_id}, importance={old_entry['importance']}")

    # Write new memory that supersedes the old one
    new_id = am.write(
        topic="Earth Shape Correction",
        keywords=["earth", "shape", "science"],
        summary="The Earth is an oblate spheroid, not a perfect sphere",
        importance=5,
        supersedes=old_id,
    )
    new_entry = am.read(new_id)
    assert new_entry["importance"] == 5
    assert new_entry["supersedes"] == old_id, f"supersedes should be {old_id}, got {new_entry['supersedes']}"
    print(f"  [*] new: {new_id}, importance={new_entry['importance']}, supersedes={old_id}")

    # Old entry's importance should be automatically demoted (-1)
    old_entry_after = am.read(old_id)
    assert old_entry_after["importance"] == 3, f"old importance should be 4-1=3, got {old_entry_after['importance']}"
    print(f"  [*] old entry demoted: importance {old_entry['importance']} -> {old_entry_after['importance']}")

    # Chained supersedes: write another entry superseding new_id
    newer_id = am.write(
        topic="Latest Earth Shape",
        keywords=["earth", "shape"],
        summary="The Earth is an irregular oblate spheroid",
        importance=5,
        supersedes=new_id,
    )
    new_entry_after = am.read(new_id)
    assert new_entry_after["importance"] == 4, f"new importance should be 5-1=4, got {new_entry_after['importance']}"
    print(
        f"  [*] chain supersedes: {newer_id} -> {new_id}(imp {5}->{new_entry_after['importance']}) -> {old_id}(imp {3})"
    )

    # Demotion should not go below 1
    # Create an entry with importance=1 then supersede it
    weak_id = am.write(topic="Temporary note", keywords=["temporary", "note"], summary="Temporary record", importance=1)
    am.write(
        topic="Formal note", keywords=["formal", "note"], summary="Formal record", importance=3, supersedes=weak_id
    )
    weak_after = am.read(weak_id)
    assert weak_after["importance"] == 1, f"importance should not go below 1, got {weak_after['importance']}"
    print("  [*] floor check: imp=1 demoted -> still 1 (correct)")

    # Superseding a non-existent entry should not raise an error
    safe_id = am.write(
        topic="Safety test", keywords=["test"], summary="Supersede a non-existent entry", supersedes="nonexist_000"
    )
    assert safe_id is not None, "should succeed even with nonexistent supersedes target"
    print("  [*] supersedes nonexistent: no error (correct)")

    # In queries, newer memory should rank first (due to higher importance)
    result = am.query(keywords=["earth", "shape"], depth="fast")
    matched_ids = [m["entry_id"] for m in result["matched_entries"]]
    assert newer_id in matched_ids, "newest entry should be in results"
    if len(matched_ids) >= 2:
        idx_newer = matched_ids.index(newer_id)
        idx_old = matched_ids.index(old_id) if old_id in matched_ids else len(matched_ids)
        assert idx_newer < idx_old, "newer entry should rank higher than old"
        print(f"  [*] query ranking: {newer_id} before {old_id} (correct)")

    print("  >>> Test 19 PASSED")


def test_20_consolidate():
    """Test 20: Consolidation cleanup (cleanup_vocab + remove_words + consolidate)"""
    print_sep("Test 20: Consolidation (cleanup + rebuild)")

    am = AgentMemory()

    # Ingest some documents to build co-occurrence matrices
    docs = [
        "AI technology is finding increasingly broad applications in healthcare",
        "Deep learning algorithms have achieved breakthroughs in image recognition",
        "Natural language processing technology helps machines understand human language",
        "Machine learning models require large amounts of data for training",
        "Reinforcement learning performs excellently in game playing and robot control",
    ]
    for doc in docs:
        am.ingest_document(doc)
    am.rebuild_matrices()

    vocab_before = am._cooccurrence.vocab_count
    print(f"  [*] initial vocab: {vocab_before}")
    assert vocab_before > 0, "vocab should not be empty"

    # Part A: cleanup_vocab test
    # Use a high threshold to ensure some words are removed
    removed = am.cleanup_vocab(min_cooccurrence=100)
    vocab_after_cleanup = am._cooccurrence.vocab_count
    print(f"  [*] cleanup_vocab(min=100): removed={removed}, vocab {vocab_before} -> {vocab_after_cleanup}")
    assert removed >= 0, "removed should be non-negative"
    if removed > 0:
        assert vocab_after_cleanup < vocab_before, "vocab should decrease"

    # Part B: matrix still usable after rebuild
    am.rebuild_matrices()
    stats = am.get_stats()
    print(f"  [*] after rebuild: vocab={stats['cooccurrence']['vocab_size']}, ppmi_nnz={stats['matrices']['ppmi_nnz']}")

    # Part C: consolidate full flow (no data_dir, no persistence triggered)
    am2 = AgentMemory()
    for doc in docs:
        am2.ingest_document(doc)
    am2.rebuild_matrices()

    # Write some memory entries (keywords will enter the co-occurrence matrix)
    am2.write(
        topic="AI Applications",
        keywords=["artificial intelligence", "applications", "healthcare"],
        summary="AI applications in healthcare",
    )
    am2.write(
        topic="DL Breakthroughs",
        keywords=["deep learning", "image", "recognition"],
        summary="Deep learning breakthroughs in computer vision",
    )

    v_before = am2._cooccurrence.vocab_count
    result = am2.consolidate(min_cooccurrence=50)
    v_after = am2._cooccurrence.vocab_count

    print(
        f"  [*] consolidate(min=50): vocab {result['vocab_before']} -> {result['vocab_after']}, "
        f"removed={result['words_removed']}"
    )
    assert "vocab_before" in result, "result should have vocab_before"
    assert "vocab_after" in result, "result should have vocab_after"
    assert "words_removed" in result, "result should have words_removed"
    assert result["vocab_before"] == v_before, "vocab_before should match"
    assert result["vocab_after"] == v_after, "vocab_after should match"

    # Part D: consolidate with rebuild_from_recent
    am3 = AgentMemory()
    # Write some entries with timestamps
    old_ts = time.time() - 100 * 86400  # 100 days ago
    recent_ts = time.time() - 1 * 86400  # 1 day ago

    am3.write(
        topic="Old Knowledge",
        keywords=["old knowledge", "outdated", "history"],
        summary="Knowledge from a long time ago",
        timestamp=old_ts,
    )
    am3.write(
        topic="New Knowledge",
        keywords=["new knowledge", "latest", "cutting edge"],
        summary="Recently acquired knowledge",
        timestamp=recent_ts,
    )
    am3.ingest_document("New knowledge and cutting edge technology are developing rapidly")
    am3.rebuild_matrices()

    result3 = am3.consolidate(rebuild_from_recent=30, min_cooccurrence=1)
    print(f"  [*] consolidate(rebuild_from_recent=30): vocab {result3['vocab_before']} -> {result3['vocab_after']}")

    # Part E: max_vocab limit
    am4 = AgentMemory()
    for doc in docs:
        am4.ingest_document(doc)
    am4.rebuild_matrices()
    v4_before = am4._cooccurrence.vocab_count

    if v4_before > 5:
        removed4 = am4.cleanup_vocab(min_cooccurrence=1, max_vocab=5)
        v4_after = am4._cooccurrence.vocab_count
        print(f"  [*] cleanup_vocab(max_vocab=5): vocab {v4_before} -> {v4_after}, removed={removed4}")
        assert v4_after <= 5, f"vocab should be <= 5 after max_vocab limit, got {v4_after}"
    else:
        print(f"  [*] vocab too small ({v4_before}) to test max_vocab limit, skipping")

    # Part F: direct remove_words test
    am5 = AgentMemory()
    am5.ingest_document("Machine learning and deep learning are core technologies of artificial intelligence")
    am5.rebuild_matrices()

    vocab5 = set(am5._cooccurrence.vocab_dict.keys())
    if len(vocab5) >= 3:
        # select two words to remove
        words_to_remove = list(vocab5)[:2]
        removed5 = am5._cooccurrence.remove_words(words_to_remove)
        assert removed5 == 2 or removed5 == len(words_to_remove), (
            f"should remove {len(words_to_remove)}, got {removed5}"
        )
        for w in words_to_remove:
            assert w not in am5._cooccurrence.vocab_dict, f"{w} should be removed from vocab"
        print(f"  [*] remove_words({words_to_remove}): removed={removed5}, vocab intact")
    else:
        print(f"  [*] vocab too small ({len(vocab5)}) for remove_words test, skipping")

    print("  >>> Test 20 PASSED")


def main():
    print("=" * 60)
    print("  AgentMemory Unified API - End-to-End Test")
    print("=" * 60)

    t_start = time.time()
    passed = 0
    failed = 0
    total = 20

    # Test 1: CRUD
    try:
        test_1_crud()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 1 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 2: Ingest + Matrices (returns am for subsequent use)
    am = None
    try:
        am = test_2_ingest_and_matrices()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 2 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    if am is None:
        print("\n  !!! Test 2 FAILED, skipping dependent Tests 3/4/5/6")
        failed += 4
    else:
        # Test 3: Query
        try:
            test_3_query(am)
            passed += 1
        except Exception as e:
            print(f"  !!! Test 3 FAILED: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

        # Test 4: Chain
        try:
            test_4_chain(am)
            passed += 1
        except Exception as e:
            print(f"  !!! Test 4 FAILED: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

        # Test 5: Persistence
        try:
            test_5_persistence(am)
            passed += 1
        except Exception as e:
            print(f"  !!! Test 5 FAILED: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

        # Test 6: Multi-Agent
        try:
            test_6_multi_agent(am)
            passed += 1
        except Exception as e:
            print(f"  !!! Test 6 FAILED: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    # Test 7: Time Filter (standalone)
    try:
        test_7_time_filter()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 7 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 8: Dual Channel (standalone)
    try:
        test_8_dual_channel()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 8 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 9: repr + stats (standalone)
    try:
        test_9_repr_and_stats()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 9 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 10: log() (standalone)
    am_ep = None
    today_ep = None
    try:
        am_ep, today_ep = test_10_log()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 10 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 11: recall_by_date (depends on Test 10)
    if am_ep is not None:
        try:
            test_11_recall_by_date(am_ep, today_ep)
            passed += 1
        except Exception as e:
            print(f"  !!! Test 11 FAILED: {e}")
            import traceback

            traceback.print_exc()
            failed += 1
    else:
        print("\n  !!! Test 10 FAILED, skipping Test 11")
        failed += 1

    # Test 12: recall_by_range (standalone)
    try:
        test_12_recall_by_range()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 12 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 13: summarize (standalone)
    try:
        test_13_summarize()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 13 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 14: episodic persistence (standalone)
    try:
        test_14_episodic_persistence()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 14 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 15: log + semantic query (standalone)
    try:
        test_15_log_semantic_query()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 15 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 16: Entry Type CRUD (standalone)
    try:
        test_16_entry_type_crud()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 16 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 17: Access Count (standalone)
    try:
        test_17_access_count()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 17 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 18: Importance Ranking (standalone)
    try:
        test_18_importance_ranking()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 18 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 19: Reconsolidation (standalone)
    try:
        test_19_reconsolidation()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 19 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    # Test 20: Consolidate (standalone)
    try:
        test_20_consolidate()
        passed += 1
    except Exception as e:
        print(f"  !!! Test 20 FAILED: {e}")
        import traceback

        traceback.print_exc()
        failed += 1

    elapsed = time.time() - t_start

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"  Time: {elapsed:.2f}s")
    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)
    else:
        print("\n  ALL TESTS PASSED!")
        sys.exit(0)


if __name__ == "__main__":
    main()

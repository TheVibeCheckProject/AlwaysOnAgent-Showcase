import datetime
from tests.test_memory import FakeEmbedder, FakeLLM, make_memory


def test_bitemporal_supersede_and_provenance_chain():
    mem = make_memory("bitemporal_chain")
    # 1. Initial fact creation
    f1, created = mem.add_fact("The user works as a frontend engineer at Company A.", "fact", 3)
    assert created
    fact1 = mem.get_fact(f1, follow_superseded=False)
    assert fact1["status"] == "active"
    assert fact1["valid_from"] is not None
    assert fact1["invalid_from"] is None
    assert fact1["superseded_by"] is None

    # 2. First supersede (job change)
    f2, created2 = mem.supersede_fact(f1, "The user works as a staff engineer at Company B.", importance=4)
    assert created2 and f2 != f1

    # Old fact is preserved with invalid_from timestamp and link
    fact1_after = mem.get_fact(f1, follow_superseded=False)
    assert fact1_after["status"] == "superseded"
    assert fact1_after["invalid_from"] is not None
    assert fact1_after["superseded_by"] == f2
    assert "Company A" in fact1_after["content"]

    # New fact is active
    fact2 = mem.get_fact(f2, follow_superseded=False)
    assert fact2["status"] == "active"
    assert fact2["valid_from"] is not None
    assert fact2["invalid_from"] is None
    assert "Company B" in fact2["content"]

    # 3. Second supersede (promotion)
    f3, created3 = mem.supersede_fact(f2, "The user works as VP of Engineering at Company B.", importance=5)
    assert created3 and f3 != f2

    # Verify provenance history
    history = mem.get_fact_history(f1)
    assert len(history) == 3
    assert history[0]["id"] == f1 and "Company A" in history[0]["content"]
    assert history[1]["id"] == f2 and "staff engineer" in history[1]["content"]
    assert history[2]["id"] == f3 and "VP of Engineering" in history[2]["content"]

    # Calling get_fact_history on any node in the chain returns the same full chain
    assert [h["id"] for h in mem.get_fact_history(f3)] == [f1, f2, f3]

    # Active facts list only contains the current active fact
    active = mem.list_facts()
    assert len(active) == 1
    assert active[0]["id"] == f3


def test_bitemporal_point_in_time_search():
    mem = make_memory("bitemporal_pit")
    conn = mem.db.get_connection()

    # Create historical facts with synthetic timestamps
    t1 = "2026-01-01T10:00:00"
    t2 = "2026-06-01T10:00:00"

    # Insert fact 1 valid from t1 until t2
    cur = conn.execute(
        "INSERT INTO mem_facts (content, category, importance, status, valid_from, invalid_from, created_at, updated_at) "
        "VALUES ('The user lives in Austin, Texas.', 'fact', 4, 'superseded', ?, ?, ?, ?)",
        (t1, t2, t1, t2)
    )
    f1 = cur.lastrowid

    # Insert fact 2 valid from t2 onward
    cur2 = conn.execute(
        "INSERT INTO mem_facts (content, category, importance, status, valid_from, invalid_from, created_at, updated_at) "
        "VALUES ('The user lives in Denver, Colorado.', 'fact', 4, 'active', ?, NULL, ?, ?)",
        (t2, t2, t2)
    )
    f2 = cur2.lastrowid
    conn.execute("UPDATE mem_facts SET superseded_by = ? WHERE id = ?", (f2, f1))
    conn.commit()

    # Rebuild FTS triggers/index
    conn.execute("INSERT INTO mem_facts_fts(rowid, content) VALUES (?, 'The user lives in Austin, Texas.')", (f1,))
    conn.execute("INSERT INTO mem_facts_fts(rowid, content) VALUES (?, 'The user lives in Denver, Colorado.')", (f2,))
    conn.commit()

    # 1. Query as of March 2026 (before the move): must return Austin
    hits_march = mem.search_facts("user lives in", at_timestamp="2026-03-15T12:00:00")
    assert any("Austin" in h["content"] for h in hits_march)
    assert not any("Denver" in h["content"] for h in hits_march)

    # 2. Query as of August 2026 (after the move): must return Denver
    hits_august = mem.search_facts("user lives in", at_timestamp="2026-08-01T12:00:00")
    assert any("Denver" in h["content"] for h in hits_august)
    assert not any("Austin" in h["content"] for h in hits_august)

    # 3. Standard active search (no timestamp): only returns current active fact
    active_hits = mem.search_facts("user lives in")
    assert any("Denver" in h["content"] for h in active_hits)
    assert not any("Austin" in h["content"] for h in active_hits)


def test_bitemporal_learning_from_exchange():
    llm = FakeLLM()
    mem = make_memory("bitemporal_learn", llm=llm)
    f_orig, _ = mem.add_fact("The user prefers morning workouts at 7am.", "preference", 3)

    llm.extract_ops = {
        "add": [],
        "update": [{"id": f_orig, "content": "The user prefers evening workouts at 6pm.", "evidence": "6pm now"}],
        "delete": [],
    }

    stats = mem.learn_from_exchange("I switched my workout schedule to 6pm now.", "Noted, 6pm workouts.")
    assert stats["updated"] == 1

    # The original fact is preserved as superseded
    orig_fact = mem.get_fact(f_orig, follow_superseded=False)
    assert orig_fact["status"] == "superseded"
    assert orig_fact["invalid_from"] is not None
    assert orig_fact["superseded_by"] is not None
    assert "7am" in orig_fact["content"]

    # Following the superseded pointer yields the active fact
    current_fact = mem.get_fact(f_orig, follow_superseded=True)
    assert current_fact["status"] == "active"
    assert "6pm" in current_fact["content"]


def test_bitemporal_forget_deletes_entire_supersede_chain():
    mem = make_memory("bitemporal_forget")
    conn = mem.db.get_connection()

    t1 = "2026-01-01T10:00:00"
    t2 = "2026-06-01T10:00:00"

    # Insert fact 1 valid from t1 until t2 (superseded)
    cur = conn.execute(
        "INSERT INTO mem_facts (content, category, importance, status, valid_from, invalid_from, created_at, updated_at) "
        "VALUES ('The user secret code is Alpha123.', 'fact', 4, 'superseded', ?, ?, ?, ?)",
        (t1, t2, t1, t2)
    )
    f1 = cur.lastrowid

    # Insert fact 2 valid from t2 onward (active)
    cur2 = conn.execute(
        "INSERT INTO mem_facts (content, category, importance, status, valid_from, invalid_from, created_at, updated_at) "
        "VALUES ('The user secret code is Beta456.', 'fact', 4, 'active', ?, NULL, ?, ?)",
        (t2, t2, t2)
    )
    f2 = cur2.lastrowid
    conn.execute("UPDATE mem_facts SET superseded_by = ? WHERE id = ?", (f2, f1))
    conn.commit()

    # Rebuild FTS
    conn.execute("INSERT INTO mem_facts_fts(rowid, content) VALUES (?, 'The user secret code is Alpha123.')", (f1,))
    conn.execute("INSERT INTO mem_facts_fts(rowid, content) VALUES (?, 'The user secret code is Beta456.')", (f2,))
    conn.commit()

    # Prior to forgetting, both versions are retrievable at their valid timestamps
    hits_t1 = mem.search_facts("secret code", at_timestamp="2026-03-15T12:00:00")
    assert any("Alpha123" in h["content"] for h in hits_t1)
    assert not any("Beta456" in h["content"] for h in hits_t1)

    hits_t2 = mem.search_facts("secret code", at_timestamp="2026-08-01T12:00:00")
    assert any("Beta456" in h["content"] for h in hits_t2)
    assert not any("Alpha123" in h["content"] for h in hits_t2)

    hits_active = mem.search_facts("secret code")
    assert any("Beta456" in h["content"] for h in hits_active)

    # Calling forget_fact on the active fact (f2) deletes both f1 and f2 across the chain
    assert mem.forget_fact(f2, scrub=True)

    # Verify both rows are completely purged from storage
    assert mem.get_fact(f1, follow_superseded=False) is None
    assert mem.get_fact(f2, follow_superseded=False) is None
    assert mem.get_fact_history(f1) == []
    assert mem.get_fact_history(f2) == []

    # Search with or without at_timestamp returns neither version
    assert mem.search_facts("secret code", at_timestamp="2026-03-15T12:00:00") == []
    assert mem.search_facts("secret code", at_timestamp="2026-08-01T12:00:00") == []
    assert mem.search_facts("secret code") == []

    # Also test forgetting when called with an older superseded fact ID directly
    f3, _ = mem.add_fact("Temporary token is TokOld999.", "fact", 3)
    f4, _ = mem.supersede_fact(f3, "Temporary token is TokNew888.", importance=3)
    assert mem.forget_fact(f3, scrub=True)
    assert mem.get_fact(f3, follow_superseded=False) is None
    assert mem.get_fact(f4, follow_superseded=False) is None
    assert mem.search_facts("Temporary token") == []


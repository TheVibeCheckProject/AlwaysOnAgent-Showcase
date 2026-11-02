"""
Tests for the assistant's long-term memory (core/memory.py)
===========================================================
A scripted fake LLM and a deterministic bag-of-words embedder stand in for Groq and the ONNX
model, so these run offline and fast. One test exercises the real local embedding model if it
is already downloaded.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# Tests never run the real TiktokVideos (it would render and send real videos): point the agent at a
# folder where it isn't installed, so an accidental call fails harmlessly
os.environ.setdefault("TIKTOK_VIDEOS_DIR", str(Path(tempfile.gettempdir()) / "no_tiktokvideos_in_tests"))
os.environ.setdefault("IMAGEGEN_DIR", str(Path(tempfile.gettempdir()) / "no_imagegen_in_tests"))
os.environ.setdefault("SOCIALPOSTENGINE_DIR", str(Path(tempfile.gettempdir()) / "no_socialpostengine_in_tests"))
os.environ.setdefault("VIBESTATS_DIR", str(Path(tempfile.gettempdir()) / "no_vibestats_in_tests"))

import core.config  # noqa: E402  (loads .env first so tests can override afterwards)
from core.database import DatabaseManager  # noqa: E402
from core.memory import MemoryManager, count_tokens, _fts_query  # noqa: E402
from core.assistant_tools import AssistantTools  # noqa: E402

TEST_DIR = Path(tempfile.mkdtemp(prefix="memory_test_"))


class FakeEmbedder:
    """Hashes words into a 384-d vector: texts sharing words are similar, identical texts identical."""
    ready = True
    DIM = 384

    def ensure_loaded(self):
        return True

    def embed(self, texts, query=False, wait=True):
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", (t or "").lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.DIM] += 1.0
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out


class FakeLLM:
    """Answers each kind of memory prompt with a scripted, recognisable response."""

    def __init__(self):
        self.calls = []
        self.extract_ops = {"add": [], "update": [], "delete": []}
        self.fail = False
        self.verdict = True          # the second opinion before a delete (True, False, or None: no answer)

    def chat(self, messages, max_tokens=800, temperature=0.6, json_mode=False, timeout=30):
        self.calls.append(messages)
        if self.fail:
            return None
        system = messages[0]["content"]
        if system.startswith("You maintain the long-term memory"):
            return json.dumps(self.extract_ops)
        if system.startswith("You double-check"):
            return None if self.verdict is None else json.dumps({"agree": self.verdict, "reason": "scripted"})
        if system.startswith("Compress this stretch"):
            return f"SUMMARY-L1 of {messages[1]['content'].count(chr(10)) + 1} lines"
        if system.startswith("Merge these consecutive"):
            return "SUMMARY-ROLLUP"
        if system.startswith("Write the assistant's core brief"):
            return "About: PROFILE-BUILT"
        if system.startswith("The user asked their assistant to forget"):
            # Rewrite every item that mentions Pepper, dropping the sentence about her
            refs = re.findall(r"^([MS]\d+): (.*)$", messages[1]["content"], re.MULTILINE)
            return json.dumps({"rewrite": [
                {"ref": ref, "content": re.sub(r"[^.]*Pepper[^.]*\.?", "", body).strip()}
                for ref, body in refs if "Pepper" in body]})
        return f"REPLY to: {messages[-1]['content'][:40]}"


def make_memory(name, llm=None, embedder=None):
    db = DatabaseManager(TEST_DIR / f"{name}.db")
    return MemoryManager(db, llm=llm if llm is not None else FakeLLM(), embedder=embedder if embedder is not None else FakeEmbedder())


def test_messages_are_stored_and_sent_as_recent_context():
    mem = make_memory("recent")
    mem.add_message("user", "My dog is called Pepper")
    mem.add_message("assistant", "Cute name!")
    mem.add_message("user", "What should I cook tonight?")
    msgs = mem.build_context("What should I cook tonight?")
    assert msgs[0]["role"] == "system" and "long-term memory" in msgs[0]["content"]
    # It can answer "where is our conversation stored / where can I see it?"
    assert "data/state.db" in msgs[0]["content"] and "Memory panel" in msgs[0]["content"]
    assert [m["content"] for m in msgs[1:-1]] == ["My dog is called Pepper", "Cute name!", "What should I cook tonight?"]
    assert msgs[-1]["role"] == "system" and "state only what is" in msgs[-1]["content"]   # rules restated last
    print("  - recent turns are sent verbatim, in order")


def test_secrets_are_redacted_before_storage():
    mem = make_memory("redact")
    real_key = os.environ.get("GROQ_API_KEY")
    os.environ["GROQ_API_KEY"] = "gsk_supersecretvalue12345"
    try:
        mid = mem.add_message("user", "my key is gsk_supersecretvalue12345 ok")
        fid, _ = mem.add_fact("The user's key is gsk_supersecretvalue12345")
    finally:
        if real_key is None:
            os.environ.pop("GROQ_API_KEY", None)
        else:
            os.environ["GROQ_API_KEY"] = real_key
    conn = mem.db.get_connection()
    stored = conn.execute("SELECT content FROM mem_messages WHERE id = ?", (mid,)).fetchone()[0]
    fact = conn.execute("SELECT content FROM mem_facts WHERE id = ?", (fid,)).fetchone()[0]
    assert "gsk_supersecretvalue12345" not in stored and "[REDACTED:GROQ_API_KEY]" in stored
    assert "gsk_supersecretvalue12345" not in fact
    print("  - API keys never reach the memory tables")


def test_duplicate_facts_are_merged_not_repeated():
    mem = make_memory("dupes")
    a, created_a = mem.add_fact("The user prefers videos posted at 9am.", "preference", 3)
    b, created_b = mem.add_fact("the user prefers videos posted at 9am", "preference", 4)
    assert created_a and not created_b and a == b
    assert mem.get_fact(a)["importance"] == 4
    assert len(mem.list_facts()) == 1
    print("  - restating a fact updates it instead of duplicating it")


def test_follow_ups_and_answers_from_the_archive():
    mem = make_memory("followup", embedder=FakeEmbedder())
    mem.add_message("user", "Let's talk about my sister Maya.")
    mem.add_message("assistant", "Sure, what about Maya?")
    mem.add_message("user", "what does she do for work?")               # respond() stores it before searching
    assert mem._search_text("what does she do for work?") == "Let's talk about my sister Maya.\nwhat does she do for work?"
    assert mem._search_text("what does this button do?") == "what does this button do?"      # not a person follow-up
    long = "she " + "word " * 20
    assert mem._search_text(long) == long                                # a long message stands on its own

    q = mem.add_message("user", "What sample rate should the voiceover audio use?")
    mem.add_message("assistant", "Use 48 kHz so it lines up with the video track.")
    with mem.db.get_connection() as conn:
        conn.execute("UPDATE mem_messages SET compressed = 1 WHERE id <= ?", (q + 1,))
    text = "\n".join(m["content"] for m in mem.build_context("voiceover audio sample rate"))
    assert "User: What sample rate should the voiceover audio use?\n  You replied: Use 48 kHz" in text, text
    assert text.count("48 kHz") == 1                                   # the reply isn't listed twice
    print("  - \"she/he\" follow-ups search with the previous message; archived questions come with their answer")


def test_hybrid_search_finds_relevant_facts():
    mem = make_memory("search")
    mem.add_fact("The user's dog is named Pepper.", "person", 3)
    mem.add_fact("The user runs example-site.com, an anonymous card site.", "project", 5)
    mem.add_fact("The user dislikes cilantro.", "preference", 2)
    hits = mem.search_facts("what's my dog's name?")
    assert hits and "Pepper" in hits[0]["content"]
    hits = mem.search_facts("example-site website")
    assert hits and "example-site" in hits[0]["content"]
    assert _fts_query('he said "drop table" OR x') == '"said" OR "drop" OR "table"'
    print("  - keyword + semantic search ranks the right fact first; FTS input is sanitized")


def test_learning_adds_updates_and_deletes_facts():
    llm = FakeLLM()
    mem = make_memory("learn", llm=llm)
    old, _ = mem.add_fact("The user posts videos at 9am.", "preference", 4)
    gone, _ = mem.add_fact("The user posts videos on weekends only.", "preference", 3)
    llm.extract_ops = {
        "add": [{"content": "The user's cat is named Biscuit.", "category": "person", "importance": 3,
                 "evidence": "my cat is Biscuit"}],
        "update": [{"id": old, "content": "The user posts videos at 10am.", "evidence": "I post videos at 10am now"},
                   {"id": 99999, "content": "hijack", "evidence": "every day"}],
        "delete": [{"id": gone, "evidence": "every day"}],
    }
    stats = mem.learn_from_exchange("Actually I post videos at 10am now, every day, and my cat is Biscuit",
                                    "Got it, 10am daily.")
    assert stats == {"added": 1, "updated": 1, "deleted": 1, "forgotten": 0}, stats
    assert mem.get_fact(old)["content"] == "The user posts videos at 10am."
    assert mem.get_fact(gone) is None and mem.get_fact(99999) is None
    assert any("Biscuit" in f["content"] for f in mem.list_facts())
    assert mem.db.get_state("memory_profile_dirty") == "1"
    print("  - extraction adds new facts, rewrites changed ones, deletes retracted ones (only ids it was shown)")


def test_memory_only_records_what_the_user_said():
    llm = FakeLLM()
    mem = make_memory("grounded", llm=llm)
    keep, _ = mem.add_fact("The user posts videos at 9am.", "preference", 4)
    # The assistant proposed options; the user said "I'm not sure!!!!". The extractor tries to save the
    # assistant's options as the user's plan, and to delete a fact, citing words the user never said.
    llm.extract_ops = {
        "add": [{"content": "The user is evaluating options A, B and C.", "category": "plan", "importance": 4,
                 "evidence": "evaluating options A, B and C"},
                {"content": "The user prioritizes architectural clarity.", "category": "preference", "importance": 3},
                {"content": "The user is unsure how to design self-healing.", "category": "fact", "importance": 2,
                 "evidence": "I'M NOT SURE"}],
        "update": [], "delete": [{"id": keep, "evidence": "delete the 9am thing"}], "forget": [],
    }
    stats = mem.learn_from_exchange("I'm not sure!!!!",
                                    "Are you evaluating options A, B and C? Maybe you prioritize architectural clarity.")
    assert stats["added"] == 1 and stats["deleted"] == 0, stats
    assert [f["content"] for f in mem.list_facts()] == ["The user posts videos at 9am.", "The user is unsure how to design self-healing."]
    print("  - proposals without the user's own words as evidence are discarded (assistant ideas, invented traits)")


def test_update_aimed_at_the_wrong_memory_never_overwrites_it():
    """Seen live with a small model: 'posting time is 7pm now' was applied as an update to a wedding-date fact."""
    llm = FakeLLM()
    mem = make_memory("wrong_update", llm=llm)
    wedding, _ = mem.add_fact("The user's sister Maya is getting married on 2026-11-14.", "person", 3)
    videos, _ = mem.add_fact("The user posts videos at 9am.", "preference", 4)
    llm.extract_ops = {"add": [], "delete": [], "update": [
        {"id": wedding, "content": "The user prefers social media videos posted at 7pm.", "evidence": "posting time to 7pm"}]}
    stats = mem.learn_from_exchange("I switched my posting time to 7pm", "Got it.")
    assert mem.get_fact(wedding)["content"] == "The user's sister Maya is getting married on 2026-11-14."   # untouched
    assert stats["updated"] == 0 and stats["added"] == 1 and any("7pm" in f["content"] for f in mem.list_facts())
    llm.extract_ops["update"] = [{"id": videos, "content": "The user posts videos at 10am.", "evidence": "10am"}]
    assert mem.learn_from_exchange("I post at 10am now", "ok")["updated"] == 1                            # real updates still apply
    assert mem.get_fact(videos)["content"] == "The user posts videos at 10am."
    print("  - an update pointed at an unrelated memory is saved as a new fact instead of overwriting it")


def test_the_current_date_is_never_saved_as_a_memory():
    """Seen live: 'Today is Wednesday, September 23 2026.' was saved, and would contradict the real date the next day."""
    from core.memory import _is_date_statement
    for stale in ("Today is Wednesday, September 23 2026.", "It is 3:15 pm.", "The current date is 2026-09-23.",
                  "Today's date is September 23.", "It's Friday."):
        assert _is_date_statement(stale), stale
    for real in ("The user's sister Maya is getting married on 2026-11-14.", "The user posts videos at 10am.",
                 "The user's mom's birthday is March 3rd.", "It is important to the user that videos post on Friday.",
                 "Today the user started a new job at the hospital on Monday, September 21 2026 in Springfield."):
        assert not _is_date_statement(real), real

    llm = FakeLLM()
    mem = make_memory("stale_date", llm=llm)
    llm.extract_ops = {"update": [], "delete": [], "add": [
        {"content": "Today is Wednesday, September 23 2026.", "category": "fact", "importance": 2, "evidence": "today"},
        {"content": "The user's dentist appointment is on 2026-10-02.", "category": "plan", "importance": 3,
         "evidence": "dentist"}]}
    stats = mem.learn_from_exchange("what's today? also my dentist is on October 2nd", "It's Wednesday.")
    assert stats["added"] == 1 and [f["content"] for f in mem.list_facts()] == ["The user's dentist appointment is on 2026-10-02."]
    print("  - a statement of today's date is never saved (it would be wrong tomorrow); dated facts still are")


def test_compression_and_rollup_keep_history_bounded():
    llm = FakeLLM()
    mem = make_memory("compress", llm=llm)
    mem.COMPRESS_TRIGGER, mem.KEEP_RECENT = 120, 40
    mem.ROLLUP_KEEP, mem.ROLLUP_MERGE = 2, 2
    filler = "lorem ipsum dolor sit amet " * 3
    for i in range(40):
        mem.add_message("user", f"question {i} {filler}")
        mem.add_message("assistant", f"answer {i} {filler}")
        mem.maybe_compress()
    s = mem.stats()
    assert s["recent_tokens"] <= mem.COMPRESS_TRIGGER, s
    assert s["summary_levels"] >= 2, s                      # summaries themselves got rolled up
    assert s["messages"] == 80                              # nothing is ever deleted from the archive
    rows = mem._recent_rows()
    assert rows[0]["role"] == "user"                        # never starts mid-exchange
    # The newest summary sits before the history, in the cacheable start of the prompt, and only there
    msgs = mem.build_context("anything")
    latest = mem.db.get_connection().execute(
        "SELECT content FROM mem_summaries WHERE rolled_up = 0 ORDER BY start_msg_id DESC LIMIT 1").fetchone()[0]
    assert msgs[1]["role"] == "system" and msgs[1]["content"].startswith("## Earlier in this conversation (compressed)")
    assert latest in msgs[1]["content"] and latest not in msgs[-1]["content"]
    assert msgs[2]["role"] == "user"                           # then the verbatim history
    # Older summaries that relate to the message ride in the note after the latest message
    ctx = mem.build_context("summary rollup")[-1]["content"]
    assert "SUMMARY" in ctx and "Earlier in your conversations" in ctx and latest not in ctx
    unrolled = mem.db.get_connection().execute(
        "SELECT level, COUNT(*) FROM mem_summaries WHERE rolled_up = 0 GROUP BY level").fetchall()
    assert all(n <= mem.ROLLUP_KEEP for _, n in unrolled), unrolled
    print(f"  - 80 messages -> {s['recent_tokens']} recent tokens + {s['summaries']} summaries (max level {s['summary_levels']})")


def test_compressed_messages_stay_searchable():
    llm = FakeLLM()
    mem = make_memory("archive", llm=llm)
    mem.COMPRESS_TRIGGER, mem.KEEP_RECENT = 60, 20
    mem.add_message("user", "The wax seal should peel off with the flap, not split in half")
    mem.add_message("assistant", "Understood, peel it off with the flap.")
    for i in range(12):
        mem.add_message("user", f"unrelated chatter number {i} about the weather today")
        mem.add_message("assistant", f"reply {i}")
    assert mem.maybe_compress()
    hits = mem.search_archive("how should the wax seal open?")
    assert hits and "wax seal" in hits[0]["content"]
    ctx = mem.build_context("how should the wax seal open?")[-1]["content"]
    assert "Related things said before" in ctx and "peel off" in ctx
    print("  - a detail that scrolled out of the window is recalled from the archive when relevant")


def test_respond_end_to_end_and_llm_outage():
    llm = FakeLLM()
    mem = make_memory("respond", llm=llm)
    llm.extract_ops = {"add": [{"content": "The user is learning Spanish.", "category": "project", "importance": 4,
                                "evidence": "started learning Spanish"}],
                       "update": [], "delete": []}
    reply = mem.respond("I started learning Spanish this week", system_context="- Daemon status: idle")
    assert reply.startswith("REPLY to:")
    mem.wait_idle()
    assert any("Spanish" in f["content"] for f in mem.list_facts())
    note = llm.calls[0][-1]["content"]
    assert "Live system status" in note and "Daemon status: idle" in note

    llm.extract_ops = {"add": [], "update": [], "delete": []}
    mem.respond("¿Cómo se dice 'memory'?")
    mem.wait_idle()
    chat = llm.calls[-2]                         # the chat call (the last call is extraction)
    assert any("learning Spanish" in m["content"] for m in chat)   # the learned fact is now used as context
    assert "Only say what's true" in chat[0]["content"] and "Tools are unavailable" in chat[0]["content"]

    llm.fail = True
    assert mem.respond("hello?") is None         # outage: no fake answer, but the message is kept
    assert mem._recent_rows()[-1]["content"] == "hello?"
    print("  - a fact learned in one turn informs the next; outages return None")


def test_a_reply_the_user_cancelled_is_not_kept():
    llm = FakeLLM()
    mem = make_memory("cancelled", llm=llm)
    llm.extract_ops = {"add": [{"content": "The user plays at casioncasino.", "category": "fact", "importance": 3,
                                "evidence": "casion"}], "update": [], "delete": []}
    assert mem.respond("okay what about casion", cancelled=lambda: True) is None
    mem.wait_idle()
    rows = [(r["role"], r["content"]) for r in mem._recent_rows()]
    assert rows[-1] == ("user", "okay what about casion")                  # the message stays, as in the app
    assert not any(role == "assistant" for role, _ in rows)                 # the reply doesn't
    assert not mem.list_facts()                                             # and nothing is learned from it
    assert mem.respond("casino", cancelled=lambda: False).startswith("REPLY to:")


def test_consolidation_merges_fades_and_builds_profile():
    llm = FakeLLM()
    mem = make_memory("consolidate", llm=llm)
    conn = mem.db.get_connection()
    emb = FakeEmbedder()
    old = "2020-01-01T00:00:00+00:00"
    for content, imp in [("The user likes green tea.", 3), ("The user likes green tea!", 3), ("The user once saw a heron.", 1)]:
        conn.execute(
            "INSERT INTO mem_facts (content, category, importance, created_at, updated_at, embedding) VALUES (?, 'fact', ?, ?, ?, ?)",
            (content, imp, old, old, emb.embed([content])[0].tobytes()))
    conn.commit()
    mem.db.set_state("memory_profile_dirty", "1")
    stats = mem.consolidate(force=True)
    assert stats["merged"] == 1 and stats["faded"] == 1 and stats["profile"] == 1, stats
    assert mem.get_profile() == "About: PROFILE-BUILT"
    assert mem.db.get_state("memory_profile_dirty") == "0"
    active = [f["content"] for f in mem.list_facts()]
    assert active == ["The user likes green tea."] or active == ["The user likes green tea!"], active
    assert "About: PROFILE-BUILT" in mem.build_context("hi")[0]["content"]
    print("  - duplicates merged, stale trivia faded, profile rebuilt and injected")


def test_forget_erases_everywhere():
    llm = FakeLLM()
    mem = make_memory("scrub", llm=llm)
    mem.COMPRESS_TRIGGER, mem.KEEP_RECENT = 60, 20
    pepper, _ = mem.add_fact("The user has a golden retriever named Pepper.", "person", 3)
    mem.add_fact("The user's name is Alex.", "profile", 5)
    mem.add_message("user", "I'm Alex. My golden retriever is named Pepper. I love hiking.")
    mem.add_message("assistant", "Nice to meet you!")
    for i in range(10):
        mem.add_message("user", f"filler message {i} about the weather")
        mem.add_message("assistant", f"ok {i}")
    mem.maybe_compress()
    conn = mem.db.get_connection()
    conn.execute("UPDATE mem_summaries SET content = 'Alex said hello. His dog is Pepper.'")
    conn.commit()

    # The user says "forget my dog": the fact is only loosely similar, but extraction still sees it
    llm.extract_ops = {"add": [], "update": [], "delete": [], "forget": [{"id": pepper, "evidence": "Forget the thing about my dog"}]}
    stats = mem.learn_from_exchange("Forget the thing about my dog, I don't want that stored.", "Done.")
    mem.wait_idle()
    assert stats["forgotten"] == 1 and mem.get_fact(pepper) is None
    everything = " ".join(r[0] for r in conn.execute(
        "SELECT content FROM mem_messages UNION ALL SELECT content FROM mem_summaries").fetchall())
    assert "Pepper" not in everything, everything
    assert "I love hiking" in everything and "Alex said hello" in everything   # the rest is kept
    assert not mem.search_archive("Pepper", compressed_only=False)
    assert mem.get_profile() == "About: PROFILE-BUILT"                          # rebuilt immediately
    print("  - 'forget' removes the fact, rewrites the archive and summaries, and rebuilds the profile")


def test_forget_memory_tool_reaches_summaries_and_facts():
    """forget_memory needs no fact id: it erased nothing when the claim lived only in summaries (2026-10-04)."""
    class ForgetLLM(FakeLLM):
        def chat(self, messages, **kw):
            if messages[0]["content"].startswith("The user asked their assistant to forget"):
                self.calls.append(messages)
                refs = re.findall(r"^([FMS]\d+): (.*)$", messages[1]["content"], re.MULTILINE)
                hit = [(r, b) for r, b in refs if "AI Studio" in b]
                # Echoes the label into the content, as Gemini did ("S7: ...")
                return json.dumps({"rewrite": [{"ref": r, "content": f"{r}: " + re.sub(r"[^.]*AI Studio[^.]*\.?", "", b).strip()}
                                               for r, b in hit if not r.startswith("F")],
                                   "forget": [r for r, _ in hit if r.startswith("F")]})
            return super().chat(messages, **kw)

    mem = make_memory("forget_tool", llm=ForgetLLM())
    keep, _ = mem.add_fact("The user lives in Springfield.", "profile", 3)
    studio, _ = mem.add_fact("The DeskAI code lives in AI Studio.", "project", 3)
    mem.add_message("user", "I like golf.")
    conn = mem.db.get_connection()
    conn.execute("INSERT INTO mem_summaries (conv, level, content, start_msg_id, end_msg_id, created_at, tokens) "
                 "VALUES ('main', 1, 'The user golfs. The DeskAI code lives in AI Studio.', 1, 1, '2026-10-01', 9)")
    conn.commit()

    mem.add_message("user", "The DeskAI code lives in AI Studio, by the way. " + "Long message. " * 150)
    mem.SCRUB_BATCH_CHARS = 120     # several erase calls, each with an answer that fits

    tools = AssistantTools(mem.db, mem, TEST_DIR, embedder=FakeEmbedder(),
                           user_text="forget that the DeskAI code lives in AI Studio")
    out = tools.forget_memory("The DeskAI code lives in AI Studio")
    assert "1 saved fact" in out and "2 past message" in out, out
    scrub_calls = [c for c in mem.llm.calls if c[0]["content"].startswith("The user asked their assistant to forget")]
    assert len(scrub_calls) >= 3
    long_msg = conn.execute("SELECT content FROM mem_messages WHERE content LIKE '%Long message%'").fetchone()[0]
    assert "AI Studio" not in long_msg and long_msg.count("Long message") == 150    # rewritten whole, not cut short
    assert mem.get_fact(studio) is None and mem.get_fact(keep)
    summary = conn.execute("SELECT content FROM mem_summaries").fetchone()[0]
    assert "AI Studio" not in summary and summary.startswith("The user golfs")
    assert "nothing was removed" in tools.forget_memory("The user owns a red boat")
    print("  - forget_memory erases a described memory from facts and summaries, and says when nothing matched")


def test_a_second_opinion_holds_deletes_it_doesnt_confirm(monkeypatch):
    """IDEAS #11: the memory's own deletes (outdated, forget, merge) go ahead only when a second AI agrees."""
    from core import memory as memory_mod
    told = []
    monkeypatch.setattr(memory_mod, "NOTIFY", told.append)
    llm = FakeLLM()
    mem = make_memory("second_opinion", llm=llm)
    golf, _ = mem.add_fact("The user enjoys playing golf a lot.", "preference", 3)
    home, _ = mem.add_fact("The user lives in Springfield.", "profile", 4)

    # It disagrees: kept, and the owner is told why
    llm.verdict = False
    llm.extract_ops = {"add": [], "update": [], "delete": [{"id": golf, "evidence": "played golf"}]}
    stats = mem.learn_from_exchange("I played golf with my brother on Saturday", "Sounds fun!")
    assert stats["deleted"] == 0 and mem.get_fact(golf)
    assert len(told) == 1 and "enjoys playing golf" in told[0] and "scripted" in told[0]
    verify = [c for c in llm.calls if c[0]["content"].startswith("You double-check")]
    assert "I played golf with my brother" in verify[-1][1]["content"] and "outdated" in verify[-1][1]["content"]

    # No answer: an outdated-delete is quietly kept; a forget the owner asked for is kept and the owner is told
    llm.verdict = None
    stats = mem.learn_from_exchange("I played golf again today", "Nice!")
    assert stats["deleted"] == 0 and mem.get_fact(golf) and len(told) == 1
    llm.extract_ops = {"add": [], "update": [], "delete": [], "forget": [{"id": home, "evidence": "forget where I live"}]}
    stats = mem.learn_from_exchange("Please forget where I live", "Done.")
    assert stats["forgotten"] == 0 and mem.get_fact(home)
    assert len(told) == 2 and "couldn't double-check" in told[1] and "forget where I live" in told[1]

    # It agrees: deleted as before
    llm.verdict = True
    stats = mem.learn_from_exchange("Please forget where I live", "Done.")
    mem.wait_idle()
    assert stats["forgotten"] == 1 and mem.get_fact(home) is None and len(told) == 2
    print("  - deletes and forgets wait for a second opinion; held ones are kept and the owner is told")


def test_merging_near_duplicates_needs_a_second_opinion():
    llm = FakeLLM()
    mem = make_memory("merge_check", llm=llm)
    conn = mem.db.get_connection()
    emb = FakeEmbedder()
    for content in ("The user likes green tea.", "The user likes green tea!"):
        conn.execute("INSERT INTO mem_facts (content, category, importance, created_at, updated_at, embedding) "
                     "VALUES (?, 'fact', 3, '2026-10-01', '2026-10-01', ?)", (content, emb.embed([content])[0].tobytes()))
    conn.commit()
    llm.verdict = None                                   # no answer: nothing merged, asked again next time
    assert mem.consolidate(force=True)["merged"] == 0 and len(mem.list_facts()) == 2
    llm.verdict = False                                  # "not the same": kept, and not asked about again
    assert mem.consolidate(force=True)["merged"] == 0
    asked = len([c for c in llm.calls if c[0]["content"].startswith("You double-check")])
    llm.verdict = True
    assert mem.consolidate(force=True)["merged"] == 0 and len(mem.list_facts()) == 2
    assert len([c for c in llm.calls if c[0]["content"].startswith("You double-check")]) == asked

    # A new fact close to an old one is only dropped as a duplicate when the second opinion agrees
    llm.verdict = False
    mem.DUPLICATE_SIM = 0.8                             # the fake embedder's scale: these two score 0.85
    fid, created = mem.add_fact("The user likes green tea a lot.", "fact", 3)
    assert created and len(mem.list_facts()) == 3
    llm.verdict = True
    assert mem.add_fact("The user likes green tea.", "fact", 3)[1] is False     # identical text: no question asked


def test_memories_use_the_owners_name():
    llm = FakeLLM()
    mem = make_memory("owner_name", llm=llm)
    assert mem.subject() == "the user"
    mem.add_fact("The user is Alex Morgan.", "profile", 5)
    assert mem.owner_name() == "Alex" and mem.subject() == "Alex"
    llm.extract_ops = {"add": [], "update": [], "delete": []}
    mem.learn_from_exchange("I like fishing on Sundays.", "Nice.")
    assert "about Alex, by name (e.g. 'Alex prefers" in llm.calls[-1][0]["content"]

    mem.rebuild_profile()
    system = llm.calls[-1][0]["content"]
    assert system.startswith("Write the assistant's core brief") and "Call them Alex" in system
    assert "No headings, bullets or lists" in system
    print("  - memories and the profile are written with the owner's name, the profile as prose")


def test_pleasantries_skip_the_extraction_call():
    llm = FakeLLM()
    mem = make_memory("skip_extract", llm=llm)
    for text in ("Thank you.", "Of course.", "👍", "Hey!", "Sounds good!!"):
        mem.learn_from_exchange(text, "You're welcome.")
    assert not llm.calls
    for text in ("yes", "Springfield", "I'm vegan"):      # could answer a question, or say something about the user
        mem.learn_from_exchange(text, "Got it.")
    assert len(llm.calls) == 3
    print("  - thanks, greetings and emoji don't cost an extraction call; short answers still do")


def test_bad_erase_rewrites_keep_the_original():
    """A rewrite that's unchanged, longer, or still about the forgotten thing is a model mistake: keep the original."""
    class SloppyLLM(FakeLLM):
        def chat(self, messages, **kw):
            if messages[0]["content"].startswith("The user asked their assistant to forget"):
                refs = dict(re.findall(r"^([MS]\d+): (.*)$", messages[1]["content"], re.MULTILINE))
                self.offered.update(r for r in refs if r.startswith("M"))
                bad = {"unchanged": lambda b: b, "longer": lambda b: b + " Also the boat is red and fast.",
                       "same": lambda b: b.replace("Fine.", "")}
                return json.dumps({"rewrite": [{"ref": r, "content": bad[b.split()[0]](b)}
                                               for r, b in refs.items() if b.split()[0] in bad]})
            return super().chat(messages, **kw)

    llm = SloppyLLM()
    llm.offered = set()
    mem = make_memory("sloppy", llm=llm)
    texts = ["unchanged: my boat is red.", "longer: my boat is red.", "same my boat is red. Fine."]
    for t in texts:
        mem.add_message("user", t)
    mem.forget_everywhere("my boat is red")
    stored = [r[0] for r in mem.db.get_connection().execute("SELECT content FROM mem_messages ORDER BY id")]
    assert stored == texts and len(llm.offered) == 3, (stored, llm.offered)
    print("  - erase rewrites that change nothing, grow, or still say the same thing are refused")


def test_extraction_sees_loosely_related_facts():
    mem = make_memory("candidates")
    fid, _ = mem.add_fact("The user has a golden retriever named Pepper.", "person", 3)
    ids = [f["id"] for f in mem._extraction_candidates("forget the thing about my dog")]
    assert fid in ids
    print("  - small memories: the extractor is shown every fact")


def test_capability_changes_are_announced_once():
    mem = make_memory("capabilities")
    mem.add_message("assistant", "I can't read my logs.")
    assert mem.note_capabilities("It can now read its logs.")
    assert not mem.note_capabilities("It can now read its logs.")          # same abilities: no repeat
    last = mem._recent_rows()[-1]
    assert last["role"] == "event" and "can now read its logs" in last["content"]
    assert mem.note_capabilities("It can now read logs and browse.")        # changed again: announced
    print("  - an ability change is recorded after the outdated claims, once per change")


def test_forget_and_pin():
    mem = make_memory("forget")
    fid, _ = mem.add_fact("The user's gym code is 4471.", "fact", 3)
    assert mem.set_pinned(fid, True) and mem.get_fact(fid)["pinned"] == 1
    assert mem.forget_fact(fid) and mem.get_fact(fid) is None
    assert not mem.search_facts("gym code")
    print("  - forgotten facts are gone from storage and search")


def test_task_briefing_and_results_flow_through_daemon():
    from core.daemon import AutonomousDaemon
    daemon = AutonomousDaemon(workspace_dir=_ROOT, db_file=str(TEST_DIR / "daemon_mem.db"),
                              log_file=str(TEST_DIR / "daemon_mem.log"))
    daemon.memory.embedder = FakeEmbedder()
    daemon.memory.VEC_MIN_FACT_KEYWORD = 0.2        # the fake embedder only counts shared words
    daemon.memory.add_fact("The user's website is built with vanilla HTML, CSS and JS only.", "instruction", 5, pinned=True)
    # Personal memories that also mention the website: tasks run on outside services, so these stay home
    daemon.memory.add_fact("The user lives in Springfield and built the website for their mom.", "profile", 5)
    daemon.memory.add_fact("The user's sister Mia helps test the website on her phone.", "person", 4)
    daemon.memory.db.set_state("memory_profile", "The user lives in Springfield, Illinois.")
    with daemon.memory.db.get_connection() as conn:
        conn.execute("INSERT INTO mem_summaries (conv, level, content, start_msg_id, end_msg_id, start_at, end_at, "
                     "created_at, tokens) VALUES ('main', 0, 'User talked about their weekend plans for the website.', "
                     "1, 4, '2026-09-23', '2026-09-23', '2026-09-23', 10)")
    task = {"id": "task-001", "title": "Add a footer", "prompt": "Add a footer to the website", "engine": "cloud"}
    briefed = daemon._with_memory_briefing(task)
    assert briefed["prompt"].endswith("[Task]\nAdd a footer to the website")
    assert "vanilla HTML" in briefed["prompt"] and task["prompt"] == "Add a footer to the website"
    for private in ("Springfield", "Mia", "weekend", "their mom"):
        assert private not in briefed["prompt"], (private, briefed["prompt"])
    assert daemon._with_memory_briefing({**task, "engine": "media_pipeline"})["prompt"] == task["prompt"]
    unrelated = {"id": "task-002", "title": "Photos", "prompt": "Rename my photos by date", "engine": "cloud"}
    assert daemon._with_memory_briefing(unrelated)["prompt"] == unrelated["prompt"]   # nothing relevant: no briefing

    daemon._remember_task_result(task, 0, "cloud", "Created footer.html")
    last = daemon.memory._recent_rows()[-1]
    assert last["role"] == "event" and "task-001" in last["content"] and "finished successfully" in last["content"]
    print("  - tasks get only relevant project notes (no profile, personal facts or conversation); "
          "their outcomes land in the shared conversation")


def test_hud_memory_api_and_page():
    os.environ.setdefault("STATE_DB_PATH", str(TEST_DIR / "hud_import.db"))   # the API server opens a DB on import
    from interfaces.hud import app as hud_app
    from interfaces.hud import common as hud_common, state as hud_state
    from tests._hud_client import LAN_ADDR, client_from

    mem = make_memory("hud")
    hud_state.set_db(mem.db)
    hud_state.set_memory(mem)
    c = client_from(hud_app)

    r = c.post("/api/memory/facts", json={"content": "The user takes their coffee black.", "category": "preference"})
    assert r.status_code == 200 and r.json()["created"], r.text
    fid = r.json()["id"]
    data = c.get("/api/memory").json()
    assert data["stats"]["facts_pinned"] == 1 and data["facts"][0]["content"].startswith("The user takes")
    assert "embedding" not in data["facts"][0]                       # binary vectors never sent to the browser
    assert c.get("/api/memory/search", params={"q": "coffee"}).json()["facts"][0]["id"] == fid

    r = c.patch(f"/api/memory/facts/{fid}", json={"content": "The user takes their coffee with oat milk.", "importance": 3, "pinned": False})
    f = r.json()["fact"]
    assert f["content"].endswith("oat milk.") and f["importance"] == 3 and f["pinned"] == 0
    assert c.patch(f"/api/memory/facts/{fid}", json={"category": "not-a-category"}).status_code == 422

    assert c.post("/api/memory/profile/rebuild").json()["profile"] == "About: PROFILE-BUILT"
    assert c.delete(f"/api/memory/facts/{fid}?erase=false").status_code == 200
    assert c.delete(f"/api/memory/facts/{fid}").status_code == 404

    for i in range(5):
        mem.add_message("user", f"message {i}")
    mem.record_event("Background task task-009 finished successfully.")
    r = c.get("/api/memory/conversation", params={"limit": 4}).json()
    assert [m["content"] for m in r["messages"]] == ["message 2", "message 3", "message 4",
                                                     "Background task task-009 finished successfully."]
    assert r["has_more"]
    r = c.get("/api/memory/conversation", params={"limit": 4, "before_id": r["messages"][0]["id"]}).json()
    assert [m["content"] for m in r["messages"]] == ["message 0", "message 1"] and not r["has_more"]

    usage = c.get("/api/memory/usage").json()
    assert usage["calls"] == 0 and usage["latest_chat"] is None and usage["cache_hit_rate"] is None

    assert c.get("/memory").status_code == 404                        # the web page is gone: DeskAI shows memory
    assert client_from(hud_app, LAN_ADDR).get("/api/memory").status_code in (401, 403)   # auth still applies
    print("  - API: add, search, edit, pin, rebuild profile, forget; protected")


class UsageLLM(FakeLLM):
    """FakeLLM that reports usage the way CloudLLM does: 1,000 prompt tokens, 600 of them cached."""
    usage_log = None

    def chat(self, messages, **kw):
        self.usage_log.record("fake-model", {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 600},
                                             "completion_tokens": 50})
        return super().chat(messages, max_tokens=kw.get("max_tokens", 800))


def test_usage_is_recorded_per_purpose_with_prompt_breakdown():
    llm = UsageLLM()
    mem = make_memory("usage", llm=llm)
    assert llm.usage_log is not None                 # memory attaches the log to its LLM client
    mem.add_fact("The user's dog is named Pepper.", "person", 3)
    llm.extract_ops = {"add": [], "update": [], "delete": []}
    mem.respond("what's my dog named?", system_context="- Daemon status: idle")
    mem.wait_idle()
    from core.llm_usage import UsageLog
    s = UsageLog(mem.db).summary()
    purposes = {r["purpose"]: r for r in s["by_purpose"]}
    assert set(purposes) == {"chat", "extract"}, purposes
    assert purposes["chat"]["counted"] == 400 and s["cache_hit_rate"] == 0.6
    assert s["chat_turns"] == 1 and s["chat_avg_counted"] == 400
    sections = s["latest_chat"]["sections"]
    assert sections["persona"] > 500 and sections["facts"] > 0 and sections["status"] > 0 and sections["history"] > 0
    assert "archive" not in sections                 # empty parts aren't reported
    print(f"  - every call is logged with its purpose; chat prompts are broken down by part: {sections}")


def test_cloud_llm_records_usage_for_each_tool_round():
    import urllib.request
    from core.llm import CloudLLM
    from core.llm_usage import UsageLog

    class FakeResponse:
        def __init__(self, body):
            self.body, self.headers = json.dumps(body).encode(), {"x-ratelimit-remaining-tokens": "7100"}

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    replies = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "get_weather", "arguments": "{}"}}]}}],
         "usage": {"prompt_tokens": 3000, "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens": 20}},
        {"choices": [{"message": {"content": "Sunny."}}],
         "usage": {"prompt_tokens": 3100, "prompt_tokens_details": {"cached_tokens": 2944}, "completion_tokens": 5,
                   "completion_tokens_details": {"reasoning_tokens": 3}}},
    ]
    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: FakeResponse(replies.pop(0))
    try:
        db = DatabaseManager(TEST_DIR / "runner_usage.db")
        runner = CloudLLM(keys={"groq": "test-key"})
        runner.usage_log = UsageLog(db)
        reply, trace = runner.chat_with_tools([{"role": "user", "content": "weather?"}], [], lambda n, a: "72F, sunny")
    finally:
        urllib.request.urlopen = real_urlopen
    assert reply == "Sunny." and len(trace) == 1
    rows = db.get_connection().execute(
        "SELECT purpose, step, prompt_tokens, cached_tokens, reasoning_tokens, remaining_tokens FROM llm_usage ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("other", 0, 3000, 0, 0, 7100), ("other", 1, 3100, 2944, 3, 7100)]
    print("  - the Groq client logs each round of a tool-using turn, with cached tokens and remaining rate limit")


def test_real_local_embedder_semantics():
    """Uses the real ONNX model if it's already downloaded (skips otherwise, never downloads)."""
    from core.embedder import MODELS_DIR, LocalEmbedder
    if not any(MODELS_DIR.rglob("model_quantized.onnx")):
        print("  - (skipped: local embedding model not downloaded)")
        return
    emb = LocalEmbedder()
    assert emb.ensure_loaded()
    mem = make_memory("real_embed", embedder=emb)
    mem.add_fact("The user wants the wax seal to peel off with the envelope flap.", "preference", 4)
    mem.add_fact("The user's dog is named Pepper.", "person", 3)
    mem.add_fact("The user posts TikToks for theexamplesite.", "project", 4)
    hits = mem.search_facts("how did I want the envelope to open?")
    assert hits and "wax seal" in hits[0]["content"], [h["content"] for h in hits]
    hits = mem.search_facts("my puppy")
    assert hits and "Pepper" in hits[0]["content"], [h["content"] for h in hits]
    print("  - real model: paraphrased questions find the right memories")


def test_token_counter():
    assert count_tokens("") == 0
    assert 3 <= count_tokens("hello there, how are you?") <= 10


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    try:
        for t in tests:
            print(f"[{t.__name__}]")
            t()
        print("\nALL MEMORY TESTS PASSED")
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)


def test_the_profile_check_catches_lists_the_user_and_invented_details():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from eval_memory import check_profile
    facts = ["Alex's full name is Alex Morgan.", "Alex is located in Springfield, Illinois.",
             "Alex enjoys playing golf a lot."]
    good = "Alex Morgan lives in Springfield, Illinois. Alex enjoys playing golf a lot."
    assert check_profile(good, facts, "Alex") == []
    assert any("the user" in p for p in check_profile("The user lives in Springfield.", facts, "Alex"))
    assert any("bullets" in p for p in check_profile("About:\n- Alex lives in Springfield.", facts, "Alex"))
    bad = check_profile("Alex lives in Springfield and has played golf at Pebble Beach 12 times.", facts, "Alex")
    assert any("Pebble" in p for p in bad) and any("12" in p for p in bad)
    print("  - the profile check catches lists, 'the user', and names or numbers the facts don't have")


def test_facts_remember_where_they_came_from():
    llm = FakeLLM()
    mem = make_memory("provenance", llm=llm)
    msg_id = mem.add_message("user", "I have a lake cabin close to Pine Lake.")
    llm.extract_ops = {"add": [{"content": "The user owns a lake cabin close to Pine Lake.", "category": "fact",
                                "importance": 3, "evidence": "lake cabin close to Pine Lake"}],
                       "update": [], "delete": []}
    mem.learn_from_exchange("I have a lake cabin close to Pine Lake.", "Nice!", msg_id=msg_id)
    fact = [f for f in mem._fact_rows() if "cabin" in f["content"]][0]
    assert fact["evidence"] == "lake cabin close to Pine Lake" and fact["source_msg_id"] == msg_id
    # An update carries the new message's words
    msg2 = mem.add_message("user", "Actually the cabin is near Cedar Falls now.")
    new_id, _ = mem.supersede_fact(fact["id"], "The user owns a lake cabin near Cedar Falls.",
                                   evidence="the cabin is near Cedar Falls", source_msg_id=msg2)
    assert mem.get_fact(new_id)["source_msg_id"] == msg2
    print("  - facts keep the owner's words and the message they came from")


def test_a_turn_whose_models_all_failed_after_tools_acted_says_what_was_done():
    """2026-10-28 audit 2.1: the reply was lost (and a tool-less fallback answered) though a tool had acted."""
    class ToolsThenOutage(FakeLLM):
        def chat_with_tools(self, messages, specs, call_tool, **kw):
            return None, [{"name": "add_to_list", "arguments": "{}", "result": "Added milk to Groceries."},
                          {"name": "web_search", "arguments": "{}", "result": "Error: search is down."}]

    class Tools:
        user_text = ""
        cancelled = None

        def tool_specs(self, **kw):
            return [{"type": "function"}]

        def call(self, name, args):
            return ""

    mem = make_memory("outage_after_tools", llm=ToolsThenOutage())
    reply = mem.respond("add milk to my groceries", tools=Tools())
    assert "stopped answering" in reply and "add_to_list: Added milk to Groceries." in reply
    assert "web_search" not in reply                      # a tool that failed did nothing worth reporting
    mem.wait_idle()
    assert mem._recent_rows()[-1]["content"] == reply     # kept, so the next turn knows it was done


def _extraction_prompt(llm):
    return next(m[1]["content"] for m in reversed(llm.calls) if m[0]["content"].startswith("You maintain the long-term"))


def test_an_answer_is_extracted_with_the_question_it_answers():
    """2026-10-28 audit 7.2: the extractor saw only "Springfield" and had to guess what it answered."""
    llm = FakeLLM()
    mem = make_memory("answer_context", llm=llm)
    mem.add_message("assistant", "Which town should I use for your weather on the Today screen?")
    mem.record_event("Background task task-9 \"Post 1 pin\" finished successfully via social_posts.")   # in between
    uid = mem.add_message("user", "Springfield")
    mem.learn_from_exchange("Springfield", "Got it, Springfield it is.", msg_id=uid)
    prompt = _extraction_prompt(llm)
    assert "the message the user was answering: Which town should I use for your weather" in prompt
    assert "Post 1 pin" not in prompt

    # A question from hours ago isn't what a new message answers
    mem2 = make_memory("answer_context_old", llm=llm)
    mem2.add_message("assistant", "Did the cabin trip happen?")
    with mem2.db.get_connection() as conn:
        conn.execute("UPDATE mem_messages SET created_at = '2026-01-01T09:00:00-06:00'")
    uid = mem2.add_message("user", "I bought a new kayak")
    mem2.learn_from_exchange("I bought a new kayak", "Nice!", msg_id=uid)
    assert "was answering" not in _extraction_prompt(llm)


def test_background_events_stay_out_of_the_window_and_the_summaries():
    """2026-10-28 audit 7.1: events were 33% of the Mini's history tokens and 20% of its summary lines."""
    llm = FakeLLM()
    mem = make_memory("away_block", llm=llm)
    mem.add_message("user", "what's up")
    mem.add_message("assistant", "Not much.")
    for i in range(8):
        mem.record_event(f"Background task task-{i} \"Post 1 pin\" finished successfully via social_posts.")
    mem.add_message("user", "anything happen while I was gone?")

    history = mem._history_messages()
    assert all("Background task" not in m["content"] for m in history)
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    away = mem._away_block()
    assert away.startswith("### While you were away") and away.count("\n- [") == mem.AWAY_EVENTS
    assert "task-7" in away and "task-0" not in away             # the newest few
    context = mem.build_context("anything happen while I was gone?")[-1]["content"]
    assert "While you were away" in context

    # Compression summarises the conversation only, and leaves no event behind uncompressed
    for i in range(40):
        mem.add_message("user", f"tell me about topic {i} " + "word " * 30)
        mem.add_message("assistant", f"topic {i} is " + "detail " * 30)
    assert mem.maybe_compress()
    summarised = next(m[1]["content"] for m in llm.calls if m[0]["content"].startswith("Compress this stretch"))
    assert "Background task" not in summarised
    left = mem.db.get_connection().execute(
        "SELECT COUNT(*) FROM mem_messages WHERE role = 'event' AND compressed = 0").fetchone()[0]
    assert left == 0


def _two_duplicates(mem):
    conn, emb = mem.db.get_connection(), FakeEmbedder()
    ids = []
    for content in ("Alex likes green tea.", "Alex likes green tea!"):
        cur = conn.execute("INSERT INTO mem_facts (content, category, importance, created_at, updated_at, embedding) "
                           "VALUES (?, 'fact', 3, '2026-10-01', '2026-10-01', ?)", (content, emb.embed([content])[0].tobytes()))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def test_a_merge_doesnt_act_on_a_fact_that_changed_meanwhile():
    """2026-10-28 audit 4.2: consolidate decided on a snapshot, waited for a second-opinion call per pair, then deleted
    without looking again, while the chat's memory worker could have changed either fact."""
    mem = make_memory("merge_race")
    a, b = _two_duplicates(mem)

    def opinion_while_the_chat_edits(question):
        for fid in (a, b):                                 # the chat's worker rewrites one of them during the call
            if "green tea!" in mem.get_fact(fid, follow_superseded=False)["content"]:
                mem.update_fact(fid, "Alex likes green tea, and matcha since October.", supersede=False)
        return True, "same"
    mem.second_opinion = opinion_while_the_chat_edits
    assert mem.consolidate(force=True)["merged"] == 0
    contents = {f["content"] for f in mem.list_facts()}
    assert "Alex likes green tea, and matcha since October." in contents and len(contents) == 2


def test_a_merged_duplicates_open_loop_moves_to_the_fact_kept():
    from core.open_loops import OpenLoops
    mem = make_memory("merge_loop")
    OpenLoops(mem.db, mem)                                 # creates mem_open_loops
    a, b = _two_duplicates(mem)
    conn = mem.db.get_connection()
    for fid in (a, b):
        conn.execute("UPDATE mem_facts SET updated_at = ? WHERE id = ?", ("2026-10-0%d" % (1 + (fid == a)), fid))
    conn.execute("INSERT INTO mem_open_loops (fact_id, status, waiting_on, created_at) "
                 "VALUES (?, 'open', 'the tea order', '2026-10-01')", (b,))                 # b is the older: dropped
    conn.commit()
    mem.second_opinion = lambda question: (True, "same")
    assert mem.consolidate(force=True)["merged"] == 1
    assert conn.execute("SELECT fact_id FROM mem_open_loops").fetchall()[0][0] == a


def test_forget_reaches_old_summaries_and_what_was_made_from_it():
    """2026-10-28 audit 4.1: the newest 40 summaries and the top 15 messages only, never the proactive messages or
    Today's cards made from the fact, and the old shadow log; the tool still said "it's gone"."""
    from core.proactive import Proactive

    class RemovesDerived(FakeLLM):
        def chat(self, messages, **kw):
            if messages[0]["content"].startswith("The user asked their assistant to forget"):
                ans = json.loads(super().chat(messages, **kw))
                ans["remove"] = re.findall(r"^(P\d+): .*Pepper", messages[1]["content"], re.MULTILINE)
                return json.dumps(ans)
            return super().chat(messages, **kw)

    db_path = TEST_DIR / "forget_wide.db"
    shadow = db_path.parent / "shadow" / "memory.jsonl"
    shadow.parent.mkdir(parents=True, exist_ok=True)
    shadow.write_text('{"input": "Pepper is a golden retriever"}\n', encoding="utf-8")
    mem = MemoryManager(DatabaseManager(db_path), llm=RemovesDerived(), embedder=FakeEmbedder())
    assert not shadow.exists()                                     # removed while shadow mode is off

    conn = mem.db.get_connection()
    for i in range(45):                                            # the oldest one mentions her
        text = "Alex's dog Pepper is a golden retriever. They hike." if i == 0 else f"Summary {i} about work."
        conn.execute("INSERT INTO mem_summaries (conv, level, content, start_msg_id, end_msg_id, start_at, end_at, "
                     "created_at, tokens) VALUES (?, 1, ?, 0, 0, '', '', '', 10)", (mem.conv, text))
    conn.commit()
    pro = Proactive(mem.db)
    pro.deliver("question", "How is Pepper doing after the vet visit?", "pepper")
    pro.deliver("notice", "Your domain renews Friday.", "renewal")

    tools = AssistantTools(mem.db, mem, TEST_DIR, embedder=FakeEmbedder(), user_text="forget about my dog Pepper")
    out = tools.forget_memory("Alex's dog Pepper")
    oldest = conn.execute("SELECT content FROM mem_summaries ORDER BY id LIMIT 1").fetchone()[0]
    assert "Pepper" not in oldest and "They hike" in oldest
    left = [r[0] for r in conn.execute("SELECT text FROM proactive_messages")]
    assert left == ["Your domain renews Friday."]
    assert "1 note(s) or card(s)" in out and "backups" in out

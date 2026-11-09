"""
The candidate pool (core/candidates.py, docs/PROACTIVE_RESEARCH.md 1b): everything the agent might say, stored with
where it came from, why, how urgent, when it expires, and what became of it.
"""

import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import candidates as cand
from core.database import DatabaseManager

TZ = datetime.datetime.now().astimezone().tzinfo


class Clock:
    def __init__(self):
        self.t = datetime.datetime(2026, 10, 14, 9, 0, tzinfo=TZ)

    def __call__(self):
        return self.t


@pytest.fixture
def pool(tmp_path):
    clock = Clock()
    return cand.CandidatePool(DatabaseManager(tmp_path / "state.db"), now=clock), clock


def test_a_candidate_is_stored_with_its_why_and_expiry(pool):
    p, clock = pool
    cid = p.add("curiosity", "question", "  What footage do you catch on the trail cam?  ",
                why="You mentioned the trail cam on Oct 5", about="Alex owns a trail cam trail camera.",
                topic="trail cam", ref="fact:19")
    c = p.get(cid)
    assert c["text"] == "What footage do you catch on the trail cam?" and c["status"] == "pending"
    assert c["why"].startswith("You mentioned") and c["ref"] == "fact:19" and c["urgency"] == "whenever"
    assert c["expires_at"] == (clock.t + datetime.timedelta(days=7)).isoformat()      # curiosity keeps a week
    assert c["scores"] == {}


@pytest.mark.parametrize("bad", [{"source": "gossip"}, {"kind": "rant"}, {"urgency": "asap"}, {"text": "  "}])
def test_bad_candidates_are_refused(pool, bad):
    p, _ = pool
    args = {"source": "curiosity", "kind": "question", "text": "A question?", "urgency": "whenever"}
    args.update(bad)
    with pytest.raises(ValueError):
        p.add(args.pop("source"), args.pop("kind"), args.pop("text"), urgency=args.pop("urgency"))


def test_pending_puts_now_first_then_by_score_and_skips_the_expired(pool):
    p, clock = pool
    a = p.add("curiosity", "question", "Curious A?")
    b = p.add("curiosity", "question", "Curious B?")
    c = p.add("signal", "notice", "GoDaddy renews tomorrow.", urgency="now")
    d = p.add("open_loop", "question", "Old news?", expires_in=datetime.timedelta(hours=1))
    p.set_score(a, 0.4, {"rank": 2})
    p.set_score(b, 0.9, {"rank": 1})
    clock.t += datetime.timedelta(hours=2)
    assert [x["id"] for x in p.pending()] == [c, b, a]          # d expired; unscored "now" still first
    assert p.get(b)["scores"] == {"rank": 1}
    assert p.expire() == 1 and p.get(d)["status"] == "lapsed" and p.get(d)["reason"] == "expired"


def test_sent_dropped_and_outcomes_are_kept_for_tuning(pool):
    p, _clock = pool
    a = p.add("curiosity", "question", "Q1?")
    b = p.add("curiosity", "question", "Q2?")
    c = p.add("interest", "notice", "New Styropyro upload.")
    p.mark_sent(a, message_id=77)
    p.drop(b, "repeat")
    p.mark_sent(b, message_id=78)                               # a dropped one can't be sent after all
    p.set_outcome(a, "answered")
    p.set_outcome(c, "answered")                                # not sent: no outcome
    assert p.get(a)["status"] == "sent" and p.get(a)["message_id"] == 77 and p.get(a)["outcome"] == "answered"
    assert p.get(b)["status"] == "dropped" and p.get(b)["reason"] == "repeat" and p.get(b)["message_id"] is None
    assert p.get(c)["outcome"] is None
    with pytest.raises(ValueError):
        p.set_outcome(a, "loved")
    counts = p.counts()
    assert counts["status"] == {"sent": 1, "dropped": 1, "pending": 1}
    assert counts["outcome"] == {"answered": 1} and counts["dropped_reason"] == {"repeat": 1}
    assert [x["id"] for x in p.recent(status="sent")] == [a]


def test_the_table_survives_a_second_pool_on_the_same_database(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    cid = cand.CandidatePool(db).add("signal", "notice", "Kept?")
    assert cand.CandidatePool(db).get(cid)["text"] == "Kept?"


# ---------------- novelty and "already discussed", in code (1.4) ----------------

FACTS = ["Alex plays golf at Maple Ridge Golf Course.", "Alex likes DIY YouTube channels such as Styropyro.",
         "Alex owns a trail cam trail camera.", "Alex runs Example Site.", "Alex lives in Springfield."]


def test_similarity_is_cached_and_survives_a_failing_embedder():
    calls = {"n": 0}

    def embed(texts):
        calls["n"] += 1
        return np.stack([np.ones(4, dtype=np.float32) for _ in texts])
    sim = cand.Similarity(embed)
    assert sim.best("a", ["b", "c"])[0] == pytest.approx(1.0)
    sim.best("a", ["b"])
    assert calls["n"] == 1                                      # all three were cached by the first call

    def broken(texts):
        raise RuntimeError("ollama down")
    assert cand.Similarity(broken).best("a", ["b"]) == (0.0, "")
    assert cand.Similarity(None).best("a", ["b"]) == (0.0, "")


def test_a_repeat_is_the_same_subject_by_the_calibrated_threshold():
    # Calibrated on the Mini: same subject >= 0.64, different <= 0.63
    def embed(texts):
        table = {"Styropyro's DIY electronics videos": [1, 0, 0], "Drake Anthony's laser projects": [0.665, 0.747, 0],
                 "trail cam footage at the cabin": [0.52, 0, 0.854]}
        return np.array([table[t] for t in texts], dtype=np.float32)
    sim = cand.Similarity(embed)
    same = cand.novelty(sim, "Drake Anthony's laser projects", ["Styropyro's DIY electronics videos"])
    other = cand.novelty(sim, "trail cam footage at the cabin", ["Styropyro's DIY electronics videos"])
    assert same["repeat"] and same["nearest"] == "Styropyro's DIY electronics videos" and same["novelty"] == pytest.approx(0.335, abs=0.01)
    assert not other["repeat"] and other["novelty"] == pytest.approx(0.48, abs=0.01)
    assert cand.novelty(sim, "trail cam footage at the cabin", [])["novelty"] == 1.0


def test_already_discussed_ignores_short_replies_and_catches_names():
    def embed(texts):
        # the subject is close to everything, as short replies were on the Mini (0.48-0.62)
        return np.array([[1.0, 0.0] if t.startswith("Stealth") else [0.6, 0.8] for t in texts], dtype=np.float32)
    sim = cand.Similarity(embed)
    short = cand.discussed(sim, "trail cam footage at the cabin", ["show me both please", "sure that sounds good"], FACTS)
    assert not short["discussed"]                              # under MIN_WORDS: never counts by meaning
    named = cand.discussed(cand.Similarity(None), "Styropyro's DIY electronics videos",
                           ["did you see what styropyro built this week"], FACTS)
    assert named["discussed"] and named["by"] == "words" and "styropyro" in named["words"]
    plain = cand.discussed(cand.Similarity(None), "golf practice at the driving range",
                           ["I need to practice my writing more"], FACTS)
    assert not plain["discussed"]                              # "practice" is a plain word, not a name


def test_already_discussed_by_meaning_needs_a_real_sentence():
    def embed(texts):
        return np.array([[1.0, 0.0] if t.startswith("how he builds") else [0.7, 0.714] for t in texts], dtype=np.float32)
    got = cand.discussed(cand.Similarity(embed), "how he builds the AI assistant with Claude",
                         ["I am more of a AI specialist when building things, I string AIs together"], FACTS)
    assert got["discussed"] and got["by"] == "meaning" and got["sim"] == pytest.approx(0.7, abs=0.01)


def test_distinctive_words_skip_the_owners_name_and_filler():
    words = cand.distinctive_words("Alex's Styropyro DIY electronics videos with his dad", FACTS)
    assert words == {"styropyro"}                              # "alex" is in most facts; plain words are not names


def test_past_messages_get_subjects_once(tmp_path):
    from core.proactive import Proactive
    db = DatabaseManager(tmp_path / "state.db")
    Proactive(db)                                               # makes proactive_messages (with its subject column)
    with db.get_connection() as c:
        for kind, text in (("question", "When you are watching Styropyro, do you have a favorite build?"),
                           ("notice", "GoDaddy renews Friday."), ("brief", "Good morning"),
                           ("question", "Do you prefer the driving range or the course?")):
            c.execute("INSERT INTO proactive_messages (kind, text, topic, created_at) VALUES (?, ?, '', '2026-10-10')",
                      (kind, text))
    asked = []

    def chat(messages):
        listing = json.loads(messages[1]["content"])
        asked.append(listing)
        return json.dumps({"subjects": [{"id": x["id"], "subject": f"subject of {x['id']}"} for x in listing]
                           + [{"id": 999, "subject": "not one of them"}]})
    assert cand.backfill_subjects(db, chat) == 3                # the brief is skipped; an unknown id is ignored
    assert {x["kind"] for x in asked[0]} == {"question", "notice"}
    assert cand.backfill_subjects(db, chat) == 0 and len(asked) == 1    # done once
    rows = db.get_connection().execute("SELECT kind, subject FROM proactive_messages ORDER BY id").fetchall()
    assert [r["subject"] for r in rows] == ["subject of 1", "subject of 2", None, "subject of 4"]
    assert cand.backfill_subjects(db, lambda m: None) == 0      # no answer: nothing changes, no error


# ---------------- ranking and the score (1.5) ----------------

SUBJECT_VECS = {
    "Styropyro's DIY electronics videos": [1.0, 0.0, 0.0, 0.0],
    "Drake Anthony's laser projects": [0.665, 0.747, 0.0, 0.0],       # same subject as Styropyro (0.665)
    "trail cam footage at the cabin": [0.0, 0.0, 1.0, 0.0],
    "golf at Maple Ridge": [0.0, 0.0, 0.0, 1.0],
}


def subject_embed(texts):
    return np.array([SUBJECT_VECS.get(t, [0.3, 0.3, 0.3, 0.3]) for t in texts], dtype=np.float32)


def make_scorer(pool, answer=None, past=(), owner=(), last=None, raise_=False):
    calls = []

    def chat(messages):
        calls.append(json.loads(messages[1]["content"]))
        if raise_:
            raise RuntimeError("rate limited")
        return json.dumps(answer) if answer is not None else None
    s = cand.Scorer(pool, cand.Similarity(subject_embed), chat, past_subjects=lambda: list(past),
                    owner_messages=lambda since: list(owner), facts=lambda: FACTS, last_sent_source=lambda: last)
    return s, calls


def test_repeats_and_what_he_is_talking_about_are_dropped_before_ranking(pool, monkeypatch):
    p, _ = pool
    rep = p.add("curiosity", "question", "What laser build of his caught your eye?", topic="Drake Anthony's laser projects")
    talked = p.add("curiosity", "question", "Do you have a favorite hole?", topic="golf at Maple Ridge")
    fresh = p.add("curiosity", "question", "What do you catch on the trail cam?", topic="trail cam footage at the cabin")
    # The question texts and his message get their own directions: the fake maps every unknown text to one vector,
    # which would make any question "already answered" by any message
    monkeypatch.setitem(SUBJECT_VECS, "What do you catch on the trail cam?", [0.0, 0.0, 0.9, 0.44])
    monkeypatch.setitem(SUBJECT_VECS, "played 18 at Maple Ridge today and shot an 89", [0.0, 1.0, 0.0, 0.0])
    s, calls = make_scorer(p, answer={"order": [fresh]}, past=["Styropyro's DIY electronics videos"],
                           owner=["played 18 at Maple Ridge today and shot an 89"])
    out = s.score_pending()
    assert [c["id"] for c in out] == [fresh]
    assert p.get(rep)["status"] == "dropped" and p.get(rep)["reason"].startswith("repeat of")
    assert p.get(talked)["status"] == "dropped" and p.get(talked)["reason"] == "already discussed (words)"
    assert [x["id"] for x in calls[0]] == [fresh]               # the ranker never sees what was dropped


def test_a_question_he_already_answered_this_week_is_dropped(pool):
    # 2026-11-01: "Did reducing those 13 nodes down to 6 make a noticeable difference in the overall smoothness?",
    # 31 hours after "there's not a single stutter at all" (measured 0.78 on the Mini; unrelated pairs <= 0.46)
    p, _ = pool
    asked = "Did reducing those 13 nodes down to 6 make it smoother?"
    told = "just got it down from 13 nodes to 6 nodes straight to it 40 second run"
    vecs = {asked: [1.0, 0.0, 0.0, 0.0], told: [0.75, 0.66, 0.0, 0.0],
            "Is the cabin feeder busy this fall?": [0.0, 0.0, 1.0, 0.0],
            "Optimizing travel path nodes": [0.0, 0.0, 0.0, 1.0]}           # the subject alone doesn't match
    embed = lambda texts: np.array([vecs.get(t, SUBJECT_VECS.get(t, [0.3, 0.3, 0.3, 0.3])) for t in texts],
                                   dtype=np.float32)
    old = p.add("curiosity", "question", asked, topic="Optimizing travel path nodes")
    new = p.add("curiosity", "question", "Is the cabin feeder busy this fall?", topic="golf at Maple Ridge")
    made = datetime.datetime.fromisoformat(p.get(old)["created_at"])

    def owner(since):                       # he said it 31 hours ago: the 24-hour look misses it, a week finds it
        return [told] if datetime.datetime.fromisoformat(since) < made - datetime.timedelta(hours=31) else []
    s = cand.Scorer(p, cand.Similarity(embed), lambda m: json.dumps({"order": [new]}), past_subjects=list,
                    owner_messages=owner, facts=lambda: FACTS, last_sent_source=lambda: None)
    out = s.score_pending()
    assert [c["id"] for c in out] == [new]
    assert p.get(old)["status"] == "dropped" and p.get(old)["reason"].startswith("already answered (0.75")


def test_the_rankers_order_and_rejections_set_the_score(pool):
    p, _ = pool
    a = p.add("curiosity", "question", "What do you catch on the trail cam?", topic="trail cam footage at the cabin")
    b = p.add("interest", "notice", "Styropyro posted a new video.", topic="Styropyro's DIY electronics videos")
    c = p.add("pattern", "question", "You've been up late a lot, you should sleep more.", topic="his sleep")
    s, _ = make_scorer(p, answer={"order": [b, a, 999], "reject": [{"id": c, "reason": "judges his behaviour"}]})
    out = s.score_pending()
    assert [x["id"] for x in out] == [b, a]
    assert p.get(c)["status"] == "dropped" and p.get(c)["reason"] == "ranker: judges his behaviour"
    top = p.get(b)
    assert top["scores"]["rank"] == 1.0 and top["scores"]["ranked"] is True
    assert top["score"] == pytest.approx(cand.RANK_WEIGHT * 1.0 + cand.NOVELTY_WEIGHT * 1.0)    # nothing asked before
    assert out[1]["score"] < out[0]["score"]


def test_the_same_source_twice_in_a_row_costs_a_little(pool):
    p, _ = pool
    a = p.add("curiosity", "question", "What do you catch on the trail cam?", topic="trail cam footage at the cabin")
    b = p.add("interest", "notice", "Styropyro posted a new video.", topic="Styropyro's DIY electronics videos")
    s, _ = make_scorer(p, answer={"order": [a, b]}, last="curiosity")
    out = {x["id"]: x for x in s.score_pending()}
    assert out[a]["scores"]["variety"] == -cand.VARIETY_PENALTY and out[b]["scores"]["variety"] == 0.0


@pytest.mark.parametrize("raise_", [False, True])
def test_without_a_ranking_novelty_still_decides_and_nothing_breaks(pool, raise_):
    p, _ = pool
    a = p.add("curiosity", "question", "What do you catch on the trail cam?", topic="trail cam footage at the cabin")
    s, _ = make_scorer(p, answer=None, raise_=raise_)
    out = s.score_pending()
    assert [x["id"] for x in out] == [a] and out[0]["scores"]["ranked"] is False
    assert out[0]["score"] == pytest.approx(cand.RANK_WEIGHT * 0.5 + cand.NOVELTY_WEIGHT * 1.0)


def test_signals_are_never_repeats_and_an_empty_pool_makes_no_call(pool):
    p, _ = pool
    s, calls = make_scorer(p, answer={"order": []})
    assert s.score_pending() == [] and calls == []
    sig = p.add("signal", "notice", "GoDaddy renews tomorrow.", urgency="now", topic="Styropyro's DIY electronics videos")
    s, _ = make_scorer(p, answer={"order": [sig]}, past=["Styropyro's DIY electronics videos"])
    assert [x["id"] for x in s.score_pending()] == [sig]       # a signal's subject may recur


# ---------------- implicit feedback: outcomes, reply rates and threshold adjustment (1e; checklist 1.8) ----------------

def test_sent_candidates_marked_answered_when_replied_within_six_hours(pool):
    p, _clock = pool
    cid = p.add("curiosity", "question", "Do you have a favorite build?")
    p.mark_sent(cid, message_id=101)

    # 2 hours later Alex replies
    replied = lambda sent_iso, deadline_iso: True
    res = p.update_outcomes(has_replied=replied)
    assert res == {"answered": 1, "ignored": 0}
    assert p.get(cid)["outcome"] == "answered"


def test_sent_candidates_marked_ignored_after_six_hours(pool):
    p, clock = pool
    cid = p.add("curiosity", "question", "Do you have a favorite build?")
    p.mark_sent(cid, message_id=101)

    # 5 hours later, no reply yet: not marked ignored yet
    clock.t += datetime.timedelta(hours=5)
    res = p.update_outcomes(has_replied=lambda a, b: False)
    assert res == {"answered": 0, "ignored": 0}
    assert p.get(cid)["outcome"] is None

    # 7 hours later: marked ignored
    clock.t += datetime.timedelta(hours=2)
    res = p.update_outcomes(has_replied=lambda a, b: False)
    assert res == {"answered": 0, "ignored": 1}
    assert p.get(cid)["outcome"] == "ignored"


def test_reply_rates_and_threshold_adjustment(pool):
    p, _clock = pool
    # Curiosity: 1 answered, 3 ignored -> rate = 0.25 (below 0.5, so threshold rises)
    for i in range(4):
        cid = p.add("curiosity", "question", f"Q{i}?")
        p.mark_sent(cid, message_id=i)
        p.set_outcome(cid, "answered" if i == 0 else "ignored")

    # Interest: 4 answered, 0 ignored -> rate = 1.0 (>= 0.8, so threshold drops slightly)
    for i in range(4):
        cid = p.add("interest", "notice", f"N{i}.")
        p.mark_sent(cid, message_id=100 + i)
        p.set_outcome(cid, "answered")

    assert p.reply_rate("curiosity") == 0.25
    assert p.reply_rate("interest") == 1.0
    assert p.reply_rate("pattern") is None

    adj_curiosity = p.threshold_adjustment("curiosity")
    adj_interest = p.threshold_adjustment("interest")
    adj_pattern = p.threshold_adjustment("pattern")

    assert adj_curiosity > 0.05       # rises due to low reply rate + last was ignored
    assert adj_interest < 0.0         # drops slightly due to high reply rate
    assert adj_pattern == 0.0         # neutral when no history


"""Tests for the cross-party (bipartisanship) stage — pure logic, no LLM/network.

The Aye/No-vs-Yea/Nay bug that motivated ALIGNMENT_QUALITY_PLAN.md silently flipped a member's
stance by dropping half their votes. This stage reads the same strings, so the directional
cases are locked in here, along with the evidence gate and the procedural-vote exclusion.
"""

import json

import pytest

from pipeline.stages import s7_bipartisanship as s7


def _vote_file(env, session, number, question, positions):
    """positions: [(bioguide, party, voteCast)]"""
    path = env / "vote_members" / f"house-vote_119_{session}_{number}_members.json"
    path.write_text(json.dumps({
        "houseRollCallVoteMemberVotes": {
            "sessionNumber": session, "rollCallNumber": number, "voteQuestion": question,
            "results": [{"bioguideID": b, "voteParty": p, "voteCast": c} for b, p, c in positions],
        }
    }))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A miniature pipeline output tree wired into the stage's module-level paths."""
    for name in ("vote_members", "members", "bills", "out"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(s7, "VOTE_MEMBERS_CACHE", tmp_path / "vote_members")
    monkeypatch.setattr(s7, "MEMBERS_DIR", tmp_path / "members")
    monkeypatch.setattr(s7, "BILLS_DIR", tmp_path / "bills")
    monkeypatch.setattr(s7, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(s7, "MIN_SIGNALS", 1)  # keep fixtures small
    return tmp_path


def _member(env, *, party="Republican", votes=(), sponsored=(), cosponsored=()):
    (env / "members" / "XX_1.json").write_text(json.dumps({
        "bioguide_id": "T000001", "name": "Test Member", "state": "XX", "district": 1,
        "party": party, "voting_history": list(votes),
        "sponsored_bills": list(sponsored), "cosponsored_bills": list(cosponsored),
    }))
    return [{"state": "XX", "district": 1, "name": "Test Member"}]


def _bill(env, number, sponsor_party, cosponsor_parties=(), bill_type="HR"):
    (env / "bills" / f"119_{bill_type}_{number}.json").write_text(json.dumps({
        "congress": 119, "bill_type": bill_type, "bill_number": number,
        "detail": {"sponsors": [{"party": sponsor_party}]},
        "cosponsors": [{"party": p} for p in cosponsor_parties],
    }))


def _result(env):
    return json.loads((env / "out" / "XX_1.json").read_text())


def test_aye_and_no_count_as_directional(env):
    """Committee-of-the-Whole votes report Aye/No, not Yea/Nay. Both must count, or half a
    member's record silently vanishes (the Barry Moore bug)."""
    _vote_file(env, 1, 1, "On Passage", [("T000001", "R", "Aye"), ("X", "R", "No"), ("Y", "R", "No")])
    cands = _member(env, votes=[{"session": 1, "vote_number": 1, "member_position": "Aye"}])
    s7.run(cands, {"congress": 119})
    r = _result(env)
    # Own party's majority was No; the member voted Aye → a defection, on 1 counted vote.
    assert r["signals"] == {**r["signals"], "votes_counted": 1, "defections": 1}
    assert r["vote_defection"] == 1.0


def test_voting_with_your_party_is_not_a_defection(env):
    _vote_file(env, 1, 1, "On Passage", [("T000001", "R", "Yea"), ("X", "R", "Yea"), ("Y", "D", "Nay")])
    cands = _member(env, votes=[{"session": 1, "vote_number": 1, "member_position": "Yea"}])
    s7.run(cands, {"congress": 119})
    assert _result(env)["vote_defection"] == 0.0


def test_procedural_votes_are_excluded(env):
    """Procedural roll calls are party-discipline noise; s5 drops them and so must this."""
    _vote_file(env, 1, 1, "On Motion to Adjourn", [("T000001", "R", "Yea"), ("X", "R", "Nay")])
    cands = _member(env, votes=[{"session": 1, "vote_number": 1, "member_position": "Yea"}])
    s7.run(cands, {"congress": 119})
    r = _result(env)
    assert r["signals"]["votes_counted"] == 0
    assert r["vote_defection"] is None


def test_cosponsor_reach_counts_the_other_partys_bills(env):
    _bill(env, "1", "D")
    _bill(env, "2", "R")
    cands = _member(env, party="Republican", cosponsored=[
        {"congress": 119, "type": "HR", "number": "1"},
        {"congress": 119, "type": "HR", "number": "2"},
    ])
    s7.run(cands, {"congress": 119})
    assert _result(env)["cosponsor_reach"] == 0.5


def test_attracted_reach_counts_own_bills_with_cross_party_cosponsors(env):
    _bill(env, "10", "R", cosponsor_parties=["D", "R"])
    _bill(env, "11", "R", cosponsor_parties=["R"])
    cands = _member(env, party="Republican", sponsored=[
        {"congress": 119, "type": "HR", "number": "10"},
        {"congress": 119, "type": "HR", "number": "11"},
    ])
    s7.run(cands, {"congress": 119})
    assert _result(env)["attracted_reach"] == 0.5


def test_thin_records_publish_no_rate(env, monkeypatch):
    """Below the evidence threshold a rate is null, not a headline 100%."""
    monkeypatch.setattr(s7, "MIN_SIGNALS", 5)
    _bill(env, "1", "D")
    cands = _member(env, cosponsored=[{"congress": 119, "type": "HR", "number": "1"}])
    s7.run(cands, {"congress": 119})
    r = _result(env)
    assert r["cosponsor_reach"] is None and r["composite"] is None and r["percentile"] is None


def test_independents_get_no_defection_rate(env):
    """An independent has no caucus majority in this data — better null than invented."""
    _vote_file(env, 1, 1, "On Passage", [("T000001", "I", "Yea"), ("X", "R", "Nay")])
    cands = _member(env, party="Independent",
                    votes=[{"session": 1, "vote_number": 1, "member_position": "Yea"}])
    s7.run(cands, {"congress": 119})
    assert _result(env)["vote_defection"] is None


def test_sponsored_bill_with_no_detail_on_disk_is_not_counted(env):
    """An unfetched bill means "we can't tell" — it must not read as "no cross-party support"."""
    cands = _member(env, sponsored=[{"congress": 119, "type": "HR", "number": "999"}])
    s7.run(cands, {"congress": 119})
    assert _result(env)["signals"]["sponsored_counted"] == 0

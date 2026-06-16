"""Day 5 verification — case-library selection (rank_cases).

Covers the spec's Verification mapping: rank_cases(job, cases) -> top 2 ids,
weighting niche/stack over description, deterministic ties, empty handling.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cases  # noqa: E402


def _case(id, title="", niche="", stack="", description="", result=""):
    return types.SimpleNamespace(
        id=id, title=title, niche=niche, stack=stack, description=description, result=result
    )


def _job(title="", description=""):
    return types.SimpleNamespace(title=title, description=description)


def test_rank_picks_relevant_top_2():
    job = _job("Unity multiplayer mobile game", "Need Photon matchmaking and Firebase backend")
    c_unity = _case(1, title="Unity co-op", niche="Unity multiplayer", stack="Unity, Photon, Firebase")
    c_web = _case(2, title="React dashboard", niche="web app", stack="React, Node")
    c_unreal = _case(3, title="Unreal shooter", niche="Unreal multiplayer", stack="Unreal, C++")

    ranked = cases.rank_cases(job, [c_unity, c_web, c_unreal], top_n=2)
    assert ranked[0].id == 1  # strongest niche/stack overlap (Unity/Photon/Firebase)
    assert len(ranked) == 2
    assert c_web not in ranked  # irrelevant web case excluded if weaker


def test_niche_stack_outweighs_description():
    job = _job("Photon multiplayer", "matchmaking lobby")
    strong = _case(1, title="x", niche="Photon multiplayer", stack="Photon")  # niche+stack hits
    weak = _case(2, title="y", description="we used photon multiplayer once long ago")  # desc only
    ranked = cases.rank_cases(job, [weak, strong], top_n=1)
    assert ranked[0].id == 1


def test_no_relevant_returns_empty():
    job = _job("Blockchain NFT casino", "crypto gambling")
    c = _case(1, title="Unity game", niche="Unity", stack="Unity")
    assert cases.rank_cases(job, [c]) == []


def test_min_score_filters_weak_matches():
    job = _job("2D Tower platformer level art", "design stage artwork")
    # only weak overlap ("art"/"design" are common) → excluded at higher threshold
    weak = _case(1, title="Unity multiplayer shooter", niche="Unity multiplayer", stack="Unity, Photon")
    assert cases.rank_cases(job, [weak], min_score=4) == []
    # but with a relevant art case it passes
    strong = _case(2, title="2D level art", niche="2D platformer level art", stack="Photoshop")
    ranked = cases.rank_cases(job, [weak, strong], top_n=2, min_score=4)
    assert strong in ranked


def test_deterministic_tie_break_by_id():
    job = _job("Unity game", "Unity")
    a = _case(5, niche="Unity")
    b = _case(2, niche="Unity")
    ranked = cases.rank_cases(job, [a, b], top_n=2)
    # equal scores → lower id first
    assert [c.id for c in ranked] == [2, 5]

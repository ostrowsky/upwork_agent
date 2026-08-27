"""Synthetic, job-tailored case generation + attachment rendering."""

import json
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job, CaseStudy  # noqa: E402
import cases  # noqa: E402
import case_artifacts  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(case_artifacts, "ARTIFACT_DIR", tmp_path / "artifacts")
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


_SAMPLE = json.dumps({
    "title": "Cross-platform Unity Runner", "niche": "Unity mobile",
    "stack": "Unity, C#, Firebase", "budget_range": "$8,000-$14,000", "duration": "10 weeks",
    "role": "Lead Unity Dev", "summary": "Shipped a 3D endless runner to iOS/Android.",
    "approach": ["Greybox in week 1", "LiveOps via Remote Config"],
    "results": ["1.2M installs", "4.6 rating", "Crash-free 99.5%"],
    "metrics": [{"label": "Installs", "value": "1.2M"}, {"label": "Rating", "value": "4.6"}],
})


def test_parse_case_json_variants():
    assert cases._parse_case_json('{"title":"X"}')["title"] == "X"
    assert cases._parse_case_json('```json\n{"title":"Y"}\n```')["title"] == "Y"
    with pytest.raises(ValueError):
        cases._parse_case_json("not json")
    with pytest.raises(ValueError):
        cases._parse_case_json('{"niche":"no title"}')


def test_generate_case_for_job_saves_and_renders(db):
    job = Job(title="Unity Mobile Game Developer", description="iOS/Android Unity game", status="READY_TO_PROPOSE")
    db.add(job)
    db.commit()
    db.refresh(job)

    res = cases.generate_case_for_job(job, None, db, llm=lambda m: _SAMPLE)
    assert res["ok"] is True
    case = db.query(CaseStudy).filter(CaseStudy.id == res["case_id"]).first()
    assert case.synthetic == 1
    assert case.job_id == job.id
    assert case.title == "Cross-platform Unity Runner"
    # Artifact files were produced and linked.
    assert res["artifact_path"] and os.path.exists(res["artifact_path"])
    assert res["png_path"] and os.path.exists(res["png_path"])
    assert case.artifact_path == res["artifact_path"]


def test_generate_case_handles_bad_llm(db):
    job = Job(title="X", description="y", status="READY_TO_PROPOSE")
    db.add(job)
    db.commit()
    res = cases.generate_case_for_job(job, None, db, llm=lambda m: "garbage")
    assert res["ok"] is False
    assert db.query(CaseStudy).count() == 0


def test_generate_case_rejects_thin_pustyshka(db):
    """A case with no results/metrics (a 'pustyshka') is rejected, not saved."""
    job = Job(title="X", description="y", status="READY_TO_PROPOSE")
    db.add(job)
    db.commit()
    thin = json.dumps({"title": "Empty case", "summary": "short", "results": [], "metrics": []})
    res = cases.generate_case_for_job(job, None, db, llm=lambda m: thin)
    assert res["ok"] is False
    assert "too thin" in res["reason"]
    assert db.query(CaseStudy).count() == 0  # nothing fabricated


def test_render_case_pdf_from_dict(tmp_path):
    out = tmp_path / "c.pdf"
    path = case_artifacts.render_case_pdf(
        {"id": 1, "title": "T", "niche": "n", "results_list": ["r1"],
         "metrics_list": [{"label": "L", "value": "V"}]},
        out,
    )
    assert os.path.exists(path)
    assert os.path.getsize(path) > 500  # a real PDF, not empty


# --- LLM returning lists where scalar text was asked for -------------------
# Observed live 2026-08-24: stack came back as ["Unity","Photon","C#"], SQLite
# refused to bind a list, and the whole Jobs page died with ProgrammingError.

def test_as_text_flattens_list():
    assert cases._as_text(["Unity", "Photon", "C#"]) == "Unity, Photon, C#"


def test_as_text_passes_string_through():
    assert cases._as_text("  Unity, C#  ") == "Unity, C#"


def test_as_text_none_and_blank_become_none():
    assert cases._as_text(None) is None
    assert cases._as_text("   ") is None
    assert cases._as_text([]) is None
    assert cases._as_text(["", "  "]) is None


def test_as_text_respects_column_limit():
    assert cases._as_text("x" * 400, 255) == "x" * 255


def test_as_text_flattens_dict():
    assert cases._as_text({"engine": "Unity"}) == "engine: Unity"


def test_generate_case_persists_when_stack_is_a_list(db):
    """The exact live failure: a list-valued stack must not break the insert."""
    payload = json.loads(_SAMPLE)
    payload["stack"] = ["Unity", "Photon", "C#", "Tiled Animator"]
    payload["niche"] = ["Mobile Pixel Art", "Multiplayer"]
    job = Job(title="Pixel art intro clip", description="Unity + Photon", status="NEW")
    db.add(job)
    db.commit()

    res = cases.generate_case_for_job(job, None, db, llm=lambda m: json.dumps(payload),
                                      render=False)

    assert res["ok"] is True, res.get("reason")
    saved = db.query(CaseStudy).filter(CaseStudy.id == res["case_id"]).first()
    assert saved.stack == "Unity, Photon, C#, Tiled Animator"
    assert saved.niche == "Mobile Pixel Art, Multiplayer"


def test_clean_text_accepts_a_list():
    # role/duration/stack reach the PDF layout straight from the LLM.
    assert case_artifacts.clean_text(["Unity", "Photon"]) == "Unity, Photon"
    assert case_artifacts.clean_text(None) == ""
    assert case_artifacts.clean_text(42) == "42"


def _case_payload(**over):
    d = {
        "title": "Co-op survival prototype",
        "niche": "Unity multiplayer",
        "stack": "Unity, Photon Fusion",
        "budget_range": "$8,000-$15,000",
        "summary": "Shipped a 4-player co-op vertical slice in ten weeks with rollback netcode.",
        "narrative": ("The studio needed a playable co-op slice for a publisher pitch. "
                      "The hard part was hiding 120ms of transatlantic latency. "
                      "We chose client-side prediction with server reconciliation over "
                      "lockstep, which would have stalled on a single slow peer. "
                      "The slice held 60fps for four players and won the pitch."),
        "approach": ["Prototype", "Netcode", "Polish"],
        "results": ["Publisher signed", "60fps with 4 players"],
        "metrics": [{"label": "FPS", "value": "60"}],
    }
    d.update(over)
    return d


def test_generated_case_stores_the_narrative(db):
    """The write-up is what lets a proposal explain HOW a similar problem was
    solved, instead of only naming the project."""
    job = Job(title="Unity co-op game", description="Need multiplayer", status="READY_TO_PROPOSE")
    db.add(job)
    db.commit()

    res = cases.generate_case_for_job(job, None, db,
                                      llm=lambda m: json.dumps(_case_payload()), render=False)
    assert res["ok"], res.get("reason")
    c = db.query(CaseStudy).filter(CaseStudy.id == res["case_id"]).first()
    assert "client-side prediction" in c.narrative
    # description stays the short summary used for ranking and list views.
    assert c.description != c.narrative


def test_case_without_narrative_still_generates(db):
    """A model that omits the field must not break generation outright."""
    job = Job(title="Unity co-op game", description="Need multiplayer", status="READY_TO_PROPOSE")
    db.add(job)
    db.commit()

    payload = _case_payload()
    payload.pop("narrative")
    res = cases.generate_case_for_job(job, None, db,
                                      llm=lambda m: json.dumps(payload), render=False)
    assert res["ok"], res.get("reason")
    c = db.query(CaseStudy).filter(CaseStudy.id == res["case_id"]).first()
    assert c.narrative is None


def test_case_prompt_demands_a_different_solution():
    """Guard the instruction that stops the case being a restatement of the job."""
    msgs = cases.build_case_gen_messages(
        type("J", (), {"title": "t", "description": "d", "budget": None})(), None)
    system = msgs[0]["content"]
    assert "narrative" in system
    assert "ДРУГОЕ решение" in system


def test_layout_renders_narrative_paragraphs():
    d = case_artifacts._layout_from_case(
        {"title": "T", "description": "s", "narrative": "Para one.\n\nPara two."})
    assert d["narrative"] == "Para one.\n\nPara two."

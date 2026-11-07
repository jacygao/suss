"""
A stand-in for a senior task's run_judge.py, with the names and call shapes
of the real one that suss's judge uses, and none of its model calls.

Each prompt is built inside the function that would send it, through
llm_tools.run_explore_and_emit or llm_utils.complete_structured, as in the
real file, so the judge has to capture it the same way.
"""

import os
import statistics
import sys
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

if not os.environ.get("REPO_NAME"):
    sys.exit(0)
REPO_PATH = Path("/repo") / os.environ["REPO_NAME"]

from ssb_lib import llm_tools  # noqa: E402
from ssb_lib.patch_classify import (  # noqa: E402
    classify_patch,
    dropped_paths,
    filter_patch_to_behavioral,
)

RUBRIC_MAX_PATCH_SIZE = 50_000
TASTE_MAX_PATCH_SIZE = 12_000
RUBRIC_SYSTEM = "You are the stand-in rubric judge. Call submit_scores."
TASTE_SYSTEM = "You are the stand-in taste judge. Call submit_taste_scores."


class CriterionScore(BaseModel):
    name: str
    score: float = Field(..., ge=0.0, le=1.0)
    reason: str

    @field_validator("score")
    @classmethod
    def _binarize(cls, v: float) -> float:
        return 1.0 if v >= 0.5 else 0.0


class RubricResult(BaseModel):
    criteria: list[CriterionScore]


class DimensionScore(BaseModel):
    score: int = Field(..., ge=1, le=5)
    rationale: str


class PracticeAlignment(BaseModel):
    style_consistency: DimensionScore
    pattern_adherence: DimensionScore
    library_usage: DimensionScore
    abstraction_level: DimensionScore
    documentation_fit: DimensionScore


class RelativeTaste(BaseModel):
    minimality: DimensionScore
    approach_quality: DimensionScore
    hygiene: DimensionScore
    fluency: DimensionScore
    craftsmanship: DimensionScore


class TasteScores(BaseModel):
    practice_alignment: PracticeAlignment
    relative_taste: RelativeTaste


def parse_rubric(rubric_path):
    import json

    data = json.loads(Path(rubric_path).read_text())
    files = data.get("files", [])
    ftp = {c["name"]: c["description"] for c in data.get("fail_to_pass", [])}
    ptp = {c["name"]: c["description"] for c in data.get("pass_to_pass", [])}
    return files, ftp, ptp


def _build_criteria_block(fail_to_pass, pass_to_pass):
    lines = [f"[{name}] {text}" for name, text in {**fail_to_pass, **pass_to_pass}.items()]
    return "\n".join(lines)


def _extract_changed_files(patch_text):
    paths = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[6:])
    return paths


def _rubric_explore_and_score(
    model, criteria_block, all_names, agent_patch_text, changed_files, extra_reference_files, repo_name
):
    user = (
        f"Evaluate the agent's work in the '{repo_name}' codebase.\n\n"
        f"## Agent's Patch\n```diff\n{agent_patch_text}\n```\n\n"
        f"## Files Changed by the Agent\n{changed_files}\n\n"
        f"## Rubric Criteria\n{criteria_block}\n\n"
        f"Use exactly these criterion names: {all_names}"
    )
    return llm_tools.run_explore_and_emit(
        model=model,
        system=RUBRIC_SYSTEM,
        user=user,
        repo_path=REPO_PATH,
        explore_tools=llm_tools.READ_TOOLS,
        emit_tool_name="submit_scores",
        emit_schema=RubricResult,
        max_turns=10,
        max_tokens=2048,
        max_file_lines=1000,
        label="rubric",
    )


def _prepare_filtered_patches(agent_text, oracle_text, *, classifier_model):
    agent_cls = classify_patch(agent_text, model=classifier_model) if agent_text.strip() else []
    oracle_cls = classify_patch(oracle_text, model=classifier_model) if oracle_text.strip() else []
    return {
        "agent_filtered": filter_patch_to_behavioral(agent_text, agent_cls),
        "oracle_filtered": filter_patch_to_behavioral(oracle_text, oracle_cls),
        "agent_classifications": agent_cls,
        "oracle_classifications": oracle_cls,
        "agent_dropped": dropped_paths(agent_cls),
        "oracle_dropped": dropped_paths(oracle_cls),
    }


def _added_lines(patch_text):
    return sum(
        1 for line in patch_text.splitlines() if line.startswith("+") and not line.startswith("+++")
    )


def assess_patch_bloat(
    agent_text, oracle_text, *, agent_filtered, oracle_filtered, agent_dropped, oracle_dropped
):
    if not agent_text.strip() or not oracle_text.strip():
        return None
    agent, oracle = _added_lines(agent_filtered), _added_lines(oracle_filtered)
    return {
        "agent_sloc": agent,
        "oracle_sloc": oracle,
        "bloat_ratio": round(agent / oracle, 3) if oracle else None,
        "agent_files_dropped": agent_dropped,
        "oracle_files_dropped": oracle_dropped,
    }


def assess_taste_with_llm(agent_patch, oracle_patch, *, model):
    user = (
        f"## Agent's Patch\n```diff\n{agent_patch}\n```\n\n"
        f"## Reference Patch (expert human baseline)\n```diff\n{oracle_patch}\n```\n\n"
        "Explore the codebase, then call submit_taste_scores with all ten dimensions."
    )
    scored = llm_tools.run_explore_and_emit(
        model=model,
        system=TASTE_SYSTEM,
        user=user,
        repo_path=REPO_PATH,
        explore_tools=llm_tools.EXPLORE_TOOLS,
        emit_tool_name="submit_taste_scores",
        emit_schema=TasteScores,
        max_turns=15,
        max_tokens=4096,
        min_explore_turns=3,
        label="taste",
    )
    return None if scored is None else _flatten_taste_scores(scored)


def _flatten_taste_scores(scores):
    pa = scores.practice_alignment.model_dump()
    rt = scores.relative_taste.model_dump()
    result = {
        "practice_alignment_score": round(statistics.median(d["score"] for d in pa.values()), 2),
        "relative_taste_score": round(statistics.median(d["score"] for d in rt.values()), 2),
    }
    for prefix, dims in (("pa_", pa), ("rt_", rt)):
        for key, dim in dims.items():
            result[f"{prefix}{key}"] = dim["score"]
            result[f"{prefix}{key}_rationale"] = dim["rationale"]
    return result

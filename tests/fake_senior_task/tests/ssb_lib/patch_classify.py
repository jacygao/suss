"""The stand-in's classifier: senior's names and fail-open rule, with paths read by line."""

from enum import Enum

from pydantic import BaseModel

SYSTEM_PROMPT = "You classify files in a unified diff. Call classify_files."


class FileCategory(str, Enum):
    BEHAVIORAL = "behavioral"
    TEST = "test"
    DOC = "doc"
    FORMATTING = "formatting"


class FileClassification(BaseModel):
    path: str
    category: FileCategory
    rationale: str


class _PatchClassifications(BaseModel):
    files: list[FileClassification]


def _sections(patch_text):
    """Each file's path and its lines, in diff order."""
    sections = []
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            sections.append([line.split(" b/", 1)[1].strip(), [line]])
        elif sections:
            sections[-1][1].append(line)
    return sections


def classify_patch(patch_text, *, model, max_tokens=4096):
    files = [path for path, _ in _sections(patch_text)]
    if not files:
        return []
    prompt = "Classify each of the following files:\n\n" + "\n".join(files)

    from . import llm_utils

    try:
        parsed = llm_utils.complete_structured(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            schema=_PatchClassifications,
            tool_name="classify_files",
            tool_description="Classify each file in the diff by its role.",
        )
    except Exception:
        parsed = None
    by_path = {c.path: c for c in parsed.files} if parsed is not None else {}
    return [
        by_path.get(path)
        or FileClassification(path=path, category=FileCategory.BEHAVIORAL, rationale="default")
        for path in files
    ]


def filter_patch_to_behavioral(patch_text, classifications):
    keep = {c.path for c in classifications if c.category == FileCategory.BEHAVIORAL}
    return "".join("".join(lines) for path, lines in _sections(patch_text) if path in keep)


def dropped_paths(classifications):
    return [c.path for c in classifications if c.category != FileCategory.BEHAVIORAL]

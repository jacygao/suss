from . import llm_utils  # noqa: F401

READ_TOOLS = ["read_file", "list_directory"]
EXPLORE_TOOLS = [*READ_TOOLS, "search_code"]


def run_explore_and_emit(**kwargs):
    raise RuntimeError("the stand-in task makes no model call")

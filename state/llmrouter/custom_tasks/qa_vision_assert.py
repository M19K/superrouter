"""Register our task and its metric with LLMRouter.

    import custom_tasks.qa_vision_assert   # triggers registration

`qa_vision_assert` is theirs-compatible: one float, exact-match on TRUE/FALSE.
The resolved quality that the routing decision should actually use lives in
`quality_axes` on each record, because a float cannot carry it.
"""
from llmrouter.evaluation import evaluation_metric
from llmrouter.utils.prompting import register_prompt


@register_prompt('qa_vision_assert', default_metric='qa_vision_assert')
def format_qa_vision_prompt(sample_data):
    return {
        "system": "You are checking a screenshot of a web page during a QA pass. "
                  "Answer with exactly one word: TRUE or FALSE.",
        "user": f"Statement: {sample_data.get('query', '')}",
    }


@evaluation_metric('qa_vision_assert')
def qa_vision_assert(prediction: str, ground_truth: str, **kwargs) -> float:
    head = prediction.strip().upper().replace("*", "").replace("`", "").lstrip("# ")
    if head.startswith("TRUE"):
        said = "TRUE"
    elif head.startswith("FALSE"):
        said = "FALSE"
    else:
        return 0.0          # no answer is a wrong answer, never a dropped one
    return 1.0 if said == ground_truth.strip().upper() else 0.0

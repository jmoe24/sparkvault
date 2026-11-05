"""The launcher's rendering of a recipe, shared by the app and sparkvault/check-recipes.py.

One implementation on purpose: the golden check proves THIS function reproduces the frozen
script's serve command, so the app can never show or run something the check did not see.
Stdlib only.
"""
import re


def render(recipe, hf_repo, include_managed=True):
    """`vllm serve <repo>` then the recipe's flags in order, as shell text.

    A value is written verbatim if already quoted; otherwise it is single-quoted when it
    contains a space or a JSON brace. `--api-key "$VLLM_API_KEY"` stays a literal reference:
    the node supplies the key, it never passes through the app.
    """
    parts = ["vllm", "serve", hf_repo]
    for f in recipe["serve"]:
        if f.get("managed") and not include_managed:
            continue
        parts.append(f["flag"])
        v = f.get("value")
        if v is not None:
            if v[:1] not in "\"'" and re.search(r"[{} ]", v):
                v = "'" + v + "'"
            parts.append(v)
    return " ".join(parts)

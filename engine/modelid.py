"""How model ids are classified.

Both the collector and the site generator need to agree on what counts as a
real model, and they disagree silently if each keeps its own copy of the rule:
a model the changelog announces but the site refuses to give a page becomes a
dead link, and a price move reported twice (once for the model, once for its
batch alias) reads as two separate events. So the rules live here, once.

stdlib only.
"""
import re

# Vendors whose published price IS their own list price. Open-weight models
# are served by many independent hosts, so their listed price is a market
# price that drifts with provider routing rather than a decision by whoever
# made the model.
FIRST_PARTY = ("anthropic/", "openai/", "google/gemini", "x-ai/")

_DATED = re.compile(r"-\d{4}$|-\d{8}$")


def is_alias(mid):
    """'~vendor/model-latest' ids are floating pointers to whatever is current,
    not models in their own right."""
    return mid.startswith("~") or "/~" in mid


def is_variant(mid):
    """A differently-priced packaging of a model that already has an entry:
    batch tiers, free tiers, previews, and pinned dated ids.

    These belong in the price table, where someone comparing offers wants to
    see them, but not in the changelog, where they double-report one decision.
    """
    tail = mid.rsplit("/", 1)[-1]
    return (":" in tail
            or tail.endswith(("-batch", "-preview", "-exp", "-latest"))
            or _DATED.search(tail) is not None)


def is_first_party(mid):
    # gpt-oss is OpenAI's open-weight line, hosted by third parties like any
    # other open model, so its price is not OpenAI's to set.
    return mid.startswith(FIRST_PARTY) and "gpt-oss" not in mid


def is_reportable(mid):
    """Whether a change to this id is worth announcing at all."""
    return not is_alias(mid) and not is_variant(mid)

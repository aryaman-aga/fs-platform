"""Narrative engine (02 §6.8): mention extraction, metric dictionary, verification and typed-text rewrites.

Public API::

    from fscore.narrative import extract_mentions, claims, compute_metrics, unresolved_sentences

``extract_mentions(text, config)`` → list of :class:`Mention` (amounts / percentages normalised to the
statement unit, signed, with period and metric); ``compute_metrics(ctx)`` → :class:`Metrics` (displayed
statement figures per period); ``unresolved_sentences(ctx)`` → sentences for the optional AI hand-off.
Corrections are published as ``ctx.shared['text_proposals']`` (typed text, never formulas).
"""

from fscore.narrative.extract import Mention, claims, extract_mentions, split_sentences
from fscore.narrative.metrics import Metrics, MetricValue, compute_metrics
from fscore.narrative.numwords import find_number_words


def unresolved_sentences(ctx):  # type: ignore[no-untyped-def]
    from fscore.checks.narrative import unresolved_sentences as _u

    return _u(ctx)


__all__ = ["Mention", "MetricValue", "Metrics", "claims", "compute_metrics", "extract_mentions", "find_number_words",
           "split_sentences", "unresolved_sentences"]

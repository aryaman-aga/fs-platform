"""AI evaluation set and harness (E-AI-02): every prompt or model change must not
regress precision / recall beyond the agreed threshold before release."""

from fscore.ai.eval.dataset import DATASET_VERSION, dataset_sha256, narrative_cases, triage_cases
from fscore.ai.eval.harness import EvalReport, compare, run_eval

__all__ = ["DATASET_VERSION", "EvalReport", "compare", "dataset_sha256", "narrative_cases", "run_eval", "triage_cases"]

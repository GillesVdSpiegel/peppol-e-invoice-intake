"""Measure the pipeline against the synthetic corpus's ground truth."""

from .evaluate import AuthenticationFailed, ConfigMismatch, EvalConfig, evaluate, summarize
from .sample import PRIORITY, select_sample
from .score import DocumentScore, score_document, score_xml

__all__ = [
    "PRIORITY",
    "AuthenticationFailed",
    "ConfigMismatch",
    "DocumentScore",
    "EvalConfig",
    "evaluate",
    "score_document",
    "score_xml",
    "select_sample",
    "summarize",
]

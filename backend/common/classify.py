"""Pluggable loan classifier. v0 is the section-9 placeholder: everything is a bridge loan."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.common.signals import ClassifierConfig, load_config


class Classification(BaseModel):
    model_config = ConfigDict(frozen=True)
    is_bridge_loan: bool
    bridge_score: int = Field(ge=0, le=100)
    classifier_version: str


Classifier = Callable[[Any, ClassifierConfig], Classification]
CLASSIFIERS: dict[str, Classifier] = {}


def register_classifier(name: str) -> Callable[[Classifier], Classifier]:
    def deco(fn: Classifier) -> Classifier:
        if name in CLASSIFIERS:
            raise ValueError(f"classifier already registered: {name}")
        CLASSIFIERS[name] = fn
        return fn

    return deco


@register_classifier("placeholder_all_true")
def placeholder_all_true(_loan: Any, config: ClassifierConfig) -> Classification:
    """Every loan is a bridge loan (unverified placeholder). Ignores its input entirely."""
    return Classification(
        is_bridge_loan=True, bridge_score=100, classifier_version=config.classifier_version
    )


def classify_loan(loan: Any, config: ClassifierConfig | None = None) -> Classification:
    """Dispatch to the classifier named in config (default: config/classifier.yaml)."""
    config = config or load_config()
    return CLASSIFIERS[config.classifier_impl](loan, config)

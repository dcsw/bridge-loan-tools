"""Placeholder classifier: always true, swappable via config."""

import pytest

from backend.common import classify as c
from backend.common.signals import load_config


@pytest.mark.parametrize(
    "payload",
    [None, {}, "junk", 42, {"signals": [], "signals_unevaluable": ["x"]}, {"anything": object()}],
)
def test_placeholder_always_true(payload):
    r = c.classify_loan(payload)
    assert r.is_bridge_loan is True
    assert r.bridge_score == 100
    assert r.classifier_version == "placeholder-all-true"


def test_swap_via_config(tmp_path):
    @c.register_classifier("always_false_test")
    def _impl(loan, config):
        return c.Classification(
            is_bridge_loan=False, bridge_score=0, classifier_version=config.classifier_version
        )

    try:
        p = tmp_path / "c.yaml"
        p.write_text(
            "classifier_version: test-v1\nclassifier_impl: always_false_test\nsignals_version: v1\n"
            "weights: {short_term: 1, private_lender: 1, purchase_then_mortgage_90d: 1, "
            "second_lien: 1, no_discharge: 1, keyword: 1}\n"
            "params: {short_term_max_months: 24, purchase_window_days: 90, keywords: [bridge]}\n"
        )
        cfg = load_config(p)
        r = c.classify_loan({}, cfg)
        assert (r.is_bridge_loan, r.bridge_score, r.classifier_version) == (False, 0, "test-v1")
    finally:
        c.CLASSIFIERS.pop("always_false_test")


def test_unknown_impl_rejected(tmp_path):
    cfg = load_config().model_copy(update={"classifier_impl": "nope"})
    with pytest.raises(KeyError, match="nope"):
        c.classify_loan({}, cfg)


def test_classification_validates():
    with pytest.raises(ValueError):
        c.Classification(is_bridge_loan=True, bridge_score=101, classifier_version="x")


def test_register_duplicate_rejected():
    with pytest.raises(ValueError):
        c.register_classifier("placeholder_all_true")(lambda loan, config: None)

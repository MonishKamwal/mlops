from quickdraw.monitoring.alert import Thresholds, evaluate

T = Thresholds()


def _drift(conf_mean: float) -> dict:
    return {
        "generated_at": "2026-07-23T12:00:00+00:00",
        "dataset_drift": {"drifted_columns": 3, "share": 1.0},
        "columns": {"confidence": {"distribution": {"current": {"mean": conf_mean}}}},
    }


def test_no_alert_when_healthy() -> None:
    # Drift is present (share 1.0) but confidence is fine and no feedback — must NOT alert,
    # because ever-present OOD drift isn't itself alert-worthy.
    decision = evaluate(_drift(0.83), None, T)
    assert decision["alert"] is False
    assert decision["reasons"] == []


def test_alert_on_confidence_collapse() -> None:
    decision = evaluate(_drift(0.40), None, T)
    assert decision["alert"] is True
    assert "mean confidence" in decision["reasons"][0]
    assert decision["title"] == "Drift alert — 2026-07-23"
    assert "confidence" in decision["body"]


def test_low_accuracy_alerts_only_with_enough_feedback() -> None:
    feedback_thin = {"accuracy": 0.2, "window": {"n": 3}}
    feedback_solid = {"accuracy": 0.2, "window": {"n": 20}}

    # 3 verdicts at 0.2 is noise — no alert
    assert evaluate(_drift(0.83), feedback_thin, T)["alert"] is False
    # 20 verdicts at 0.2 is a real signal — alert
    decision = evaluate(_drift(0.83), feedback_solid, T)
    assert decision["alert"] is True
    assert "proxy accuracy" in decision["reasons"][0]


def test_good_accuracy_does_not_alert() -> None:
    feedback = {"accuracy": 0.82, "window": {"n": 50}}
    assert evaluate(_drift(0.83), feedback, T)["alert"] is False


def test_empty_feedback_accuracy_is_ignored() -> None:
    # A quiet week (accuracy null) must not alert on the accuracy rule.
    feedback = {"accuracy": None, "window": {"n": 0}}
    assert evaluate(_drift(0.83), feedback, T)["alert"] is False


def test_both_reasons_can_fire() -> None:
    feedback = {"accuracy": 0.1, "window": {"n": 30}}
    decision = evaluate(_drift(0.30), feedback, T)
    assert decision["alert"] is True
    assert len(decision["reasons"]) == 2


def test_thresholds_are_configurable() -> None:
    # A stricter confidence floor turns a previously-fine window into an alert.
    strict = Thresholds(min_confidence=0.9)
    assert evaluate(_drift(0.83), None, strict)["alert"] is True

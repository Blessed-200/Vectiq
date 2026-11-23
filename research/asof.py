"""Clock checks shared by the offline feature replay and its negative tests."""


def _time_ms(name, value):
    if type(value) is not int or value < 0:
        raise ValueError(f"Missing or invalid millisecond clock for {name}")
    return value


def assert_asof(alert_ms, latency_ms, features, previous_matches=()):
    """Validate all the source availability clocks at the decision information cutoff."""
    alert = _time_ms("alert", alert_ms)
    latency = _time_ms("latency", latency_ms)
    cutoff = alert - latency
    if not features:
        raise ValueError("Missing feature availability clocks")
    for name, available in features.items():
        if _time_ms(name, available) > cutoff:
            raise ValueError(f"{name} arrived after the information cutoff")
    for started, final_known in previous_matches:
        if _time_ms("start", started) > _time_ms("final_known", final_known):
            raise ValueError("Inconsistent match clocks")
        if final_known > cutoff:
            raise ValueError("A historical match final was unavailable at the cutoff")
    return cutoff


def assert_quote_execution(decision_ms, accepted_ms, offered_ms, last_valid_ms,
                           accepted_odds, accepted_stake):
    """Run only on evidence of a bookmaker fill; an inferred TTL cannot establish a fill."""
    import math
    clocks = [_time_ms(k, v) for k, v in zip(
        ("offer", "acceptance", "validity"), (offered_ms, accepted_ms, last_valid_ms))]
    if not clocks[0] <= clocks[1] <= clocks[2] or _time_ms("decision", decision_ms) > clocks[1]:
        raise ValueError("Quote or decision time inconsistent with acceptance")
    for label, value, lower in (("odds", accepted_odds, 1), ("stake", accepted_stake, 0)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= lower:
            raise ValueError(f"Missing or invalid accepted {label}")

from datetime import date, datetime, timedelta, timezone

import pytest

from conversation import promise as P
from conversation.case import Clock, Promise

CREATED = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


def test_lane_b_default_times():
    plan = P.compute_promise(CREATED, "unrecognized_charge", country="MX")
    assert isinstance(plan.promise, Promise) and isinstance(plan.clock, Clock)
    assert plan.promise.expected_date == date(2026, 10, 27)  # p90 = 28 days
    assert (plan.promise.sla_days, plan.promise.p90_days) == (15, 28)
    assert plan.clock.assigned_by == CREATED + timedelta(hours=24)
    assert plan.clock.first_response_by == CREATED + timedelta(hours=24)
    assert plan.clock.sla_alert_at == CREATED + timedelta(days=12)  # 80 % of 15 days
    assert plan.clock.breach_at == CREATED + timedelta(days=15)
    assert plan.times_version == P.load_times()["version"]


def test_order_of_the_clock():
    c = P.compute_promise(CREATED, "wrong_fee").clock
    assert c.assigned_by <= c.first_response_by < c.sla_alert_at < c.breach_at


def test_fraud_queue_answers_in_two_hours():
    plan = P.compute_promise(CREATED, "unrecognized_charge", queue="fraud", country="AR")
    assert plan.clock.first_response_by == CREATED + timedelta(hours=2)
    assert plan.clock.assigned_by == CREATED + timedelta(hours=2)
    assert plan.clock.breach_at == CREATED + timedelta(days=15)


def test_local_day_decides_the_expected_date():
    late = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)  # still 29 Sep in Mexico City
    assert P.compute_promise(late, country="MX").promise.expected_date == date(2026, 10, 27)
    assert P.compute_promise(late, country=None).promise.expected_date == date(2026, 10, 28)


def test_naive_datetime_is_utc_and_result_is_utc():
    plan = P.compute_promise(CREATED.replace(tzinfo=None))
    assert plan.clock.breach_at == CREATED + timedelta(days=15)
    assert plan.clock.breach_at.utcoffset() == timedelta(0)


def test_times_file_cites_its_source_and_validates(tmp_path):
    text = P.PROMISE_PATH.read_text(encoding="utf-8")
    assert "analisis_quejas" in text and "median 16" in text and "p90 28" in text
    bad = tmp_path / "bad.yaml"
    bad.write_text(text.replace("expected_basis: p90_days", "expected_basis: soon"), encoding="utf-8")
    with pytest.raises(ValueError):
        P.load_times(bad)
    bad.write_text(text.replace("    assign_hours: 2\n", "    assign_hours: 2\n    typo_hours: 1\n"), encoding="utf-8")
    with pytest.raises(ValueError):
        P.load_times(bad)


def test_case_record_accepts_the_plan():
    from conversation.case import CaseRecord
    plan = P.compute_promise(CREATED, "wrong_fee", country="AR")
    rec = CaseRecord(case_id="EV-TEST0001", created_at=CREATED, channel="web", language="pt",
                     customer_id="DEMO-C-0002", promise=plan.promise, clock=plan.clock)
    assert rec.promise.expected_date == date(2026, 10, 27)

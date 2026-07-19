"""Integration tests for POST /api/contracts/{id}/analyse."""
import pytest

import analyze_contract

FULL_RESULT = {
    "risk_score": "Low",
    "missing_clauses": [],
    "problematic_terms": [],
    "payment_terms_ok": True,
    "key_obligations": ["Pay within 30 days"],
    "recommendations": "",
    "confidence": 0.9,
}


def _stub_run_analysis(monkeypatch, result):
    monkeypatch.setattr(
        analyze_contract, "run_analysis", lambda pdf_path, rules_path="compliance_rules.json": result
    )


def test_analyse_success_response_and_persistence(client, make_contract, fetch_contract, monkeypatch):
    """Full success path: response and persisted row both show review_type=Agent, reviewer=AI Agent, and a reviewed_at timestamp."""
    contract_id = make_contract()
    _stub_run_analysis(monkeypatch, FULL_RESULT)

    resp = client.post(f"/api/contracts/{contract_id}/analyse")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "Agent Cleared"
    assert body["result"] == FULL_RESULT

    row = fetch_contract(contract_id)
    assert row["status"] == "Agent Cleared"
    assert row["review_type"] == "Agent"
    assert row["reviewer"] == "AI Agent"
    assert row["reviewed_at"] is not None
    assert row["risk_score"] == "Low"


@pytest.mark.parametrize(
    "risk_score,expected_status",
    [
        ("Low", "Agent Cleared"),
        ("Medium", "Flagged — Needs Attention"),
        ("High", "Escalated for Legal Review"),
        ("Unknown", "Agent Review Failed"),
    ],
)
def test_analyse_status_mapping(
    client, make_contract, fetch_contract, monkeypatch, risk_score, expected_status
):
    """The status_from_risk() contract, checked in both the HTTP response and the database row."""
    contract_id = make_contract(filename=f"contract_{risk_score}.pdf")
    _stub_run_analysis(monkeypatch, dict(FULL_RESULT, risk_score=risk_score))

    resp = client.post(f"/api/contracts/{contract_id}/analyse")

    assert resp.status_code == 200
    assert resp.json()["status"] == expected_status

    row = fetch_contract(contract_id)
    assert row["status"] == expected_status
    assert row["risk_score"] == risk_score


@pytest.mark.parametrize("payment_terms_ok", [True, False, None])
def test_analyse_payment_terms_ok_persisted(
    client, make_contract, fetch_contract, monkeypatch, payment_terms_ok
):
    """payment_terms_ok round-trips through SQLite (bool -> INTEGER -> back)."""
    contract_id = make_contract(filename=f"contract_pt_{payment_terms_ok}.pdf")
    _stub_run_analysis(monkeypatch, dict(FULL_RESULT, payment_terms_ok=payment_terms_ok))

    resp = client.post(f"/api/contracts/{contract_id}/analyse")

    assert resp.status_code == 200
    row = fetch_contract(contract_id)
    if payment_terms_ok is None:
        assert row["payment_terms_ok"] is None
    else:
        assert bool(row["payment_terms_ok"]) == payment_terms_ok

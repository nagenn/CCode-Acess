"""Unit tests for analyze_contract.run_analysis()."""
import json

import pytest

import analyze_contract

VALID_RESULT = {
    "risk_score": "Low",
    "missing_clauses": [],
    "problematic_terms": [],
    "payment_terms_ok": True,
    "key_obligations": ["Deliver services within 30 days"],
    "recommendations": "",
    "confidence": 0.92,
}


def test_run_analysis_success(monkeypatch, tmp_path, mock_openai_response):
    """A valid model response passes through run_analysis() unchanged."""
    monkeypatch.setattr(
        analyze_contract, "extract_contract_text", lambda pdf_path: "This is a services agreement..."
    )
    mock_openai_response(json.dumps(VALID_RESULT))

    result = analyze_contract.run_analysis(str(tmp_path / "irrelevant.pdf"))

    assert result == VALID_RESULT


def test_run_analysis_empty_pdf_fallback(monkeypatch, tmp_path, mock_openai_response):
    """Blank extracted text short-circuits to the Unknown fallback; OpenAI is never called."""
    monkeypatch.setattr(analyze_contract, "extract_contract_text", lambda pdf_path: "   ")
    calls = mock_openai_response(json.dumps(VALID_RESULT))

    result = analyze_contract.run_analysis(str(tmp_path / "empty.pdf"))

    assert result == {
        "risk_score": "Unknown",
        "missing_clauses": [],
        "problematic_terms": [],
        "payment_terms_ok": None,
        "key_obligations": [],
        "recommendations": "Could not extract text from PDF.",
        "confidence": 0.0,
    }
    assert calls == []  # OpenAI must never be called for unreadable/empty PDFs


@pytest.mark.parametrize("payment_terms_ok", [True, False, None])
def test_run_analysis_payment_terms_ok_passthrough(
    monkeypatch, tmp_path, mock_openai_response, payment_terms_ok
):
    """payment_terms_ok survives run_analysis() exactly as the model returned it."""
    monkeypatch.setattr(analyze_contract, "extract_contract_text", lambda pdf_path: "Payment due Net 45.")
    payload = dict(VALID_RESULT, payment_terms_ok=payment_terms_ok)
    mock_openai_response(json.dumps(payload))

    result = analyze_contract.run_analysis(str(tmp_path / "contract.pdf"))

    assert result["payment_terms_ok"] == payment_terms_ok

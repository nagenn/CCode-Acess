"""Integration tests for GET /api/vendors/{vendor_name}/summary."""
import app as app_module


def _insert_contract(filename, vendor, status, uploaded_at, review_type=None, confidence=None):
    conn = app_module.get_db()
    conn.execute(
        """
        INSERT INTO contracts (filename, vendor, uploaded_at, status, review_type, confidence)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (filename, vendor, uploaded_at, status, review_type, confidence),
    )
    conn.commit()
    conn.close()


def test_vendor_summary_counts_and_most_recent(client):
    """Bucket counts, average confidence, and most-recent-by-upload-date all reflect the vendor's rows."""
    _insert_contract("a.pdf", "Acme Corp", "Escalated for Legal Review", "2026-01-01T00:00:00", "Agent", 0.9)
    _insert_contract("b.pdf", "Acme Corp", "Flagged — Needs Attention", "2026-01-02T00:00:00", "Agent", 0.6)
    _insert_contract("c.pdf", "Acme Corp", "Agent Cleared", "2026-01-03T00:00:00", "Agent", 0.95)

    resp = client.get("/api/vendors/Acme Corp/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["vendor"] == "Acme Corp"
    assert body["total_contracts"] == 3
    assert body["escalated"] == 1
    assert body["flagged"] == 1
    assert body["cleared"] == 1
    assert body["most_recent_status"] == "Agent Cleared"
    assert body["average_confidence"] == round((0.9 + 0.6 + 0.95) / 3, 2)


def test_vendor_summary_excludes_stale_confidence_from_manual_override(client):
    """A contract agent-reviewed then later manually overridden keeps a leftover confidence
    value in its row (manual_review() never clears it) -- the average must gate on
    review_type == 'Agent', not just confidence IS NOT NULL, or that stale value leaks in."""
    _insert_contract("a.pdf", "Beta LLC", "Agent Cleared", "2026-01-01T00:00:00", "Agent", 0.9)
    conn = app_module.get_db()
    conn.execute("UPDATE contracts SET review_type='Manual', status='Manually Cleared' WHERE filename='a.pdf'")
    conn.commit()
    conn.close()
    _insert_contract("b.pdf", "Beta LLC", "Escalated for Legal Review", "2026-01-02T00:00:00", "Agent", 0.5)

    resp = client.get("/api/vendors/Beta LLC/summary")

    assert resp.json()["average_confidence"] == 0.5


def test_vendor_summary_no_agent_reviews_average_is_null(client):
    """A vendor with only manual (or unreviewed) contracts has no agent confidence data at all."""
    _insert_contract("a.pdf", "Gamma Inc", "Manually Cleared", "2026-01-01T00:00:00", "Manual", None)

    resp = client.get("/api/vendors/Gamma Inc/summary")

    assert resp.json()["average_confidence"] is None


def test_vendor_summary_case_insensitive_match(client):
    """Vendor lookup is case-insensitive; the response echoes back the stored casing."""
    _insert_contract("a.pdf", "Delta Systems", "Pending Review", "2026-01-01T00:00:00")

    resp = client.get("/api/vendors/delta systems/summary")

    assert resp.status_code == 200
    assert resp.json()["vendor"] == "Delta Systems"


def test_vendor_summary_unknown_vendor_404(client):
    resp = client.get("/api/vendors/Nonexistent Vendor/summary")

    assert resp.status_code == 404

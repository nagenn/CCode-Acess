import os
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

# analyze_contract.py builds its OpenAI client at import time from this env
# var. Tests never make a real API call (the client method is always
# monkeypatched), so a dummy value is enough to let the import succeed.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-key-for-tests")

import analyze_contract  # noqa: E402
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeChatResponse:
    """Mimics the bits of an OpenAI ChatCompletion response run_analysis() reads."""

    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


@pytest.fixture
def mock_openai_response(monkeypatch):
    """Stubs analyze_contract's OpenAI call. Returns the recorded call list."""

    def _install(content):
        calls = []

        def fake_create(*args, **kwargs):
            calls.append((args, kwargs))
            return _FakeChatResponse(content)

        monkeypatch.setattr(analyze_contract.client.chat.completions, "create", fake_create)
        return calls

    return _install


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(app_module, "DB_PATH", path)
    app_module.init_db()
    yield path
    os.remove(path)


@pytest.fixture
def contracts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "CONTRACTS_FOLDER", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(temp_db):
    return TestClient(app_module.app)


@pytest.fixture
def make_contract(temp_db, contracts_dir):
    """Factory: writes a placeholder PDF + matching DB row, returns the row id."""

    def _make(filename="test_contract.pdf", status="Pending Review"):
        (contracts_dir / filename).write_bytes(b"%PDF-1.4 placeholder\n")
        conn = app_module.get_db()
        conn.execute(
            "INSERT INTO contracts (filename, vendor, uploaded_at, status) VALUES (?, ?, ?, ?)",
            (filename, "Test Vendor", "2026-07-19T00:00:00", status),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM contracts WHERE filename=?", (filename,)).fetchone()
        conn.close()
        return row["id"]

    return _make


@pytest.fixture
def fetch_contract(temp_db):
    """Reads a contract row back from the (patched) DB after a request."""

    def _fetch(contract_id):
        conn = app_module.get_db()
        row = conn.execute("SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
        conn.close()
        return row

    return _fetch

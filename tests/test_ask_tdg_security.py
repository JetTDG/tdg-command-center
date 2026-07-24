import pytest

from app.ask_tdg_security import execute_read_only_query, validate_ask_payload


class FakeCursor:
    description = [("count",)]

    def __init__(self):
        self.executed = []
        self.closed = False

    def execute(self, sql):
        self.executed.append(sql)

    def fetchmany(self, size):
        assert size == 21
        return [(48,)]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.session_args = None
        self.rolled_back = False
        self.closed = False

    def set_session(self, **kwargs):
        self.session_args = kwargs

    def cursor(self):
        return self.cursor_instance

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_validate_ask_payload_bounds_question_and_history():
    question, history = validate_ask_payload({
        "question": " Active listings? ",
        "history": [
            {"role": "user", "text": "Earlier question"},
            {"role": "assistant", "text": "Earlier answer"},
        ],
    })
    assert question == "Active listings?"
    assert history == [
        {"role": "user", "text": "Earlier question"},
        {"role": "assistant", "text": "Earlier answer"},
    ]

    with pytest.raises(ValueError, match="valid JSON object"):
        validate_ask_payload([])
    with pytest.raises(ValueError, match="2,000 characters"):
        validate_ask_payload({"question": "x" * 2001})
    with pytest.raises(ValueError, match="12 messages"):
        validate_ask_payload({"question": "ok", "history": [{}] * 13})


def test_execute_read_only_query_enforces_transaction_timeout_and_row_bound(monkeypatch):
    connection = FakeConnection()
    captured = {}

    def fake_connect(database_url, **kwargs):
        captured["database_url"] = database_url
        captured["kwargs"] = kwargs
        return connection

    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/tdg")
    monkeypatch.setattr("app.ask_tdg_security.psycopg2.connect", fake_connect)

    result = execute_read_only_query("SELECT COUNT(*) FROM transactions;")

    assert captured == {
        "database_url": "postgresql://database.example/tdg",
        "kwargs": {"connect_timeout": 10},
    }
    assert connection.session_args == {"readonly": True, "autocommit": False}
    assert connection.cursor_instance.executed == [
        "SET LOCAL statement_timeout = 10000",
        "SELECT COUNT(*) FROM transactions",
    ]
    assert result.columns == ["count"]
    assert result.rows == [(48,)]
    assert result.truncated is False
    assert connection.rolled_back is True
    assert connection.cursor_instance.closed is True
    assert connection.closed is True


@pytest.mark.parametrize("sql", [
    "DELETE FROM transactions",
    "SELECT 1; DROP TABLE transactions",
    "WITH changed AS (DELETE FROM transactions RETURNING *) SELECT * FROM changed",
])
def test_execute_read_only_query_rejects_non_select_or_multiple_statements(monkeypatch, sql):
    monkeypatch.setenv("DATABASE_URL", "postgresql://database.example/tdg")
    monkeypatch.setattr(
        "app.ask_tdg_security.psycopg2.connect",
        lambda *args, **kwargs: pytest.fail("unsafe SQL reached the database"),
    )

    with pytest.raises(ValueError, match="single SELECT"):
        execute_read_only_query(sql)
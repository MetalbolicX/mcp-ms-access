"""Tests for facade-adapter contract: signature drift, false-success propagation, readonly gates.

Plan 042 — facade↔adapter contract alignment.
"""

from ms_access_mcp.application.database import DatabaseUseCases
from ms_access_mcp.errors import Err
from ms_access_mcp.models import QueryResult, WriteResult


class SpyAdapter:
    """Spy that records all calls and returns OK-like success by default."""

    def __init__(self):
        self.connected = True
        self._calls = []

    def _record(self, method: str, args: tuple, kwargs: dict):
        self._calls.append((method, args, kwargs))

    def _ok(self, **kw) -> WriteResult:
        return WriteResult(success=True, affected=kw.get("affected", 0), error=kw.get("error"))

    def _fail(self, msg="failure") -> WriteResult:
        return WriteResult(success=False, error=msg)

    def execute_query(self, sql: str, params=None) -> QueryResult:
        return QueryResult(success=True, rows=[], count=0, columns=[])

    def connect(self, db_path: str) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def get_tables(self):
        return []

    def get_queries(self):
        return []

    def get_database_properties(self):
        return {}

    def get_database_statistics(self):
        return {}

    def insert_data(self, table_name: str, data: dict) -> WriteResult:
        self._record("insert_data", (table_name, data), {})
        return self._ok()

    def update_data(self, table_name: str, data: dict, where_dict: str | None = None) -> WriteResult:
        self._record("update_data", (table_name, data), {"where_dict": where_dict})
        return self._ok()

    def delete_data(self, table_name: str, where_dict: str | None = None) -> WriteResult:
        self._record("delete_data", (table_name,), {"where_dict": where_dict})
        return self._ok()

    def create_query(self, name: str, sql: str) -> WriteResult:
        self._record("create_query", (name, sql), {})
        return self._ok()

    def set_query_sql(self, name: str, sql: str) -> WriteResult:
        self._record("set_query_sql", (name, sql), {})
        return self._ok()

    def delete_query(self, name: str) -> WriteResult:
        self._record("delete_query", (name,), {})
        return self._ok()

    def create_table(self, table_name: str, columns: dict) -> WriteResult:
        self._record("create_table", (table_name, columns), {})
        return self._ok()

    def alter_table(self, table_name: str, action: str, column_name: str | None = None, sql_type: str | None = None) -> WriteResult:
        self._record("alter_table", (table_name, action), {"column_name": column_name, "sql_type": sql_type})
        return self._ok()

    def delete_table(self, table_name: str) -> WriteResult:
        self._record("delete_table", (table_name,), {})
        return self._ok()

    def create_index(self, table_name: str, index_name: str, columns: list, *, unique: bool = False) -> WriteResult:
        self._record("create_index", (table_name, index_name, columns), {"unique": unique})
        return self._ok()

    def drop_index(self, table_name: str, index_name: str) -> WriteResult:
        self._record("drop_index", (table_name, index_name), {})
        return self._ok()

    def create_relationship(self, table_name: str, relationship_name: str, columns: list, foreign_table: str, foreign_columns: list) -> WriteResult:
        self._record("create_relationship", (table_name, relationship_name, columns, foreign_table, foreign_columns), {})
        return self._ok()

    def delete_relationship(self, table_name: str, *, relationship_name: str) -> WriteResult:
        self._record("delete_relationship", (table_name,), {"relationship_name": relationship_name})
        return self._ok()


class FailingSpyAdapter(SpyAdapter):
    """Spy that returns a failure result instead of success."""

    def insert_data(self, table_name: str, data: dict) -> WriteResult:
        return self._fail("insert failed")

    def update_data(self, table_name: str, data: dict, where_dict: str | None = None) -> WriteResult:
        return self._fail("update failed")

    def delete_data(self, table_name: str, where_dict: str | None = None) -> WriteResult:
        return self._fail("delete failed")

    def create_query(self, name: str, sql: str) -> WriteResult:
        return self._fail("create_query failed")

    def set_query_sql(self, name: str, sql: str) -> WriteResult:
        return self._fail("set_query_sql failed")

    def delete_query(self, name: str) -> WriteResult:
        return self._fail("delete_query failed")

    def create_table(self, table_name: str, columns: dict) -> WriteResult:
        return self._fail("create_table failed")

    def alter_table(self, table_name: str, action: str, column_name: str | None = None, sql_type: str | None = None) -> WriteResult:
        return self._fail("alter_table failed")

    def delete_table(self, table_name: str) -> WriteResult:
        return self._fail("delete_table failed")

    def create_index(self, table_name: str, index_name: str, columns: list, *, unique: bool = False) -> WriteResult:
        return self._fail("create_index failed")

    def drop_index(self, table_name: str, index_name: str) -> WriteResult:
        return self._fail("drop_index failed")

    def create_relationship(self, table_name: str, relationship_name: str, columns: list, foreign_table: str, foreign_columns: list) -> WriteResult:
        return self._fail("create_relationship failed")

    def delete_relationship(self, table_name: str, *, relationship_name: str) -> WriteResult:
        return self._fail("delete_relationship failed")


def make_db(Spy=FailingSpyAdapter, readonly=False):
    """Build a DatabaseUseCases wired to a spy adapter on the default connection."""
    db = DatabaseUseCases(readonly=readonly)
    db._connections["default"] = Spy()
    return db


class TestSignatureConformance:
    """Regression: facade must call adapter methods with correct signatures.

    Uses inspect.signature to verify that for every write method on DaoAdapter,
    the arguments emitted by the facade bind cleanly to the adapter's signature
    (no TypeError from unexpected kwargs or missing required args).
    """

    def test_update_data_where_kwarg(self):
        """Facade calls adapter.update_data(table, data, where_dict=where) — not where=."""
        spy = SpyAdapter()
        db = DatabaseUseCases(readonly=False)
        db._connections["default"] = spy
        db.update_data("Users", {"name": "Alice"}, where="id=1", confirm=True)
        _, args, kwargs = spy._calls[-1]
        assert args == ("Users", {"name": "Alice"})
        assert "where_dict" in kwargs
        assert kwargs["where_dict"] == "id=1"

    def test_delete_data_where_kwarg(self):
        """Facade calls adapter.delete_data(table, where_dict=where) — not where=."""
        spy = SpyAdapter()
        db = DatabaseUseCases(readonly=False)
        db._connections["default"] = spy
        db.delete_data("Users", where="id=1", confirm=True)
        _, args, kwargs = spy._calls[-1]
        assert args == ("Users",)
        assert "where_dict" in kwargs
        assert kwargs["where_dict"] == "id=1"

    def test_create_relationship_five_args(self):
        """Facade calls adapter.create_relationship(source_table, rel_name, [col], ft, [fcol])."""
        spy = SpyAdapter()
        db = DatabaseUseCases(readonly=False)
        db._connections["default"] = spy
        db.create_relationship("FK_Users", "Users", "id", "Orders", "user_id", confirm=True)
        _, args, kwargs = spy._calls[-1]
        assert args == ("Users", "FK_Users", ["id"], "Orders", ["user_id"])

    def test_delete_relationship_table_name_kwarg(self):
        """Facade calls adapter.delete_relationship('', relationship_name=name) — not positional."""
        spy = SpyAdapter()
        db = DatabaseUseCases(readonly=False)
        db._connections["default"] = spy
        db.delete_relationship("FK_Users", confirm=True)
        _, args, kwargs = spy._calls[-1]
        assert args == ("",)
        assert kwargs.get("relationship_name") == "FK_Users"


class TestFalseSuccessPropagation:
    """Facade must return Err when adapter returns success=False."""

    def test_insert_data_false_success(self):
        r = make_db().insert_data("Users", {"id": 1}, confirm=True)
        assert isinstance(r, Err)
        assert "insert failed" in str(r)

    def test_update_data_false_success(self):
        r = make_db().update_data("Users", {"id": 1}, where="id=1", confirm=True)
        assert isinstance(r, Err)
        assert "update failed" in str(r)

    def test_delete_data_false_success(self):
        r = make_db().delete_data("Users", where="id=1", confirm=True)
        assert isinstance(r, Err)
        assert "delete failed" in str(r)

    def test_create_query_false_success(self):
        r = make_db().create_query("q", "SELECT 1", confirm=True)
        assert isinstance(r, Err)
        assert "create_query failed" in str(r)

    def test_set_query_sql_false_success(self):
        r = make_db().set_query_sql("q", "SELECT 2", confirm=True)
        assert isinstance(r, Err)
        assert "set_query_sql failed" in str(r)

    def test_delete_query_false_success(self):
        r = make_db().delete_query("q", confirm=True)
        assert isinstance(r, Err)
        assert "delete_query failed" in str(r)

    def test_create_table_false_success(self):
        r = make_db().create_table("NewTable", {"id": "INT"}, confirm=True)
        assert isinstance(r, Err)
        assert "create_table failed" in str(r)

    def test_alter_table_false_success(self):
        r = make_db().alter_table("Users", "add", column_name="age", sql_type="INT", confirm=True)
        assert isinstance(r, Err)
        assert "alter_table failed" in str(r)

    def test_delete_table_false_success(self):
        r = make_db().delete_table("Users", confirm=True)
        assert isinstance(r, Err)
        assert "delete_table failed" in str(r)

    def test_create_index_false_success(self):
        r = make_db().create_index("Users", "idx_name", ["name"], confirm=True)
        assert isinstance(r, Err)
        assert "create_index failed" in str(r)

    def test_drop_index_false_success(self):
        r = make_db().drop_index("Users", "idx_name", confirm=True)
        assert isinstance(r, Err)
        assert "drop_index failed" in str(r)

    def test_create_relationship_false_success(self):
        r = make_db().create_relationship("FK", "Users", "id", "Orders", "user_id", confirm=True)
        assert isinstance(r, Err)
        assert "create_relationship failed" in str(r)

    def test_delete_relationship_false_success(self):
        r = make_db().delete_relationship("FK", confirm=True)
        assert isinstance(r, Err)
        assert "delete_relationship failed" in str(r)


class TestReadonlyGate:
    """Write methods must be blocked in readonly mode."""

    def test_insert_data_readonly(self):
        r = make_db(readonly=True).insert_data("Users", {"id": 1}, confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_update_data_readonly(self):
        r = make_db(readonly=True).update_data("Users", {"id": 1}, where="id=1", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_delete_data_readonly(self):
        r = make_db(readonly=True).delete_data("Users", where="id=1", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_create_query_readonly(self):
        r = make_db(readonly=True).create_query("q", "SELECT 1", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_set_query_sql_readonly(self):
        r = make_db(readonly=True).set_query_sql("q", "SELECT 2", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_delete_query_readonly(self):
        r = make_db(readonly=True).delete_query("q", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_create_table_readonly(self):
        r = make_db(readonly=True).create_table("T", {"id": "INT"}, confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_alter_table_readonly(self):
        r = make_db(readonly=True).alter_table("Users", "add", column_name="x", sql_type="INT", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_delete_table_readonly(self):
        r = make_db(readonly=True).delete_table("Users", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_create_index_readonly(self):
        r = make_db(readonly=True).create_index("Users", "idx", ["name"], confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_drop_index_readonly(self):
        r = make_db(readonly=True).drop_index("Users", "idx", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_create_relationship_readonly(self):
        r = make_db(readonly=True).create_relationship("FK", "Users", "id", "Orders", "uid", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

    def test_delete_relationship_readonly(self):
        r = make_db(readonly=True).delete_relationship("FK", confirm=True)
        assert isinstance(r, Err)
        assert "readonly" in str(r).lower()

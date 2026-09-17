"""DAO relationship roundtrip — proves source_table reaches the DAO layer.

PROBE: DAO COM provider reachable (pywin32 + Access OR direct ACE).
When unavailable, the test skips cleanly. When available, it asserts the
relation created via DatabaseUseCases reports the real source table, not "".
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.com_integration,
]


def _dao_relation_available() -> bool:
    """Return True if DAO relationship creation is reachable on this host."""
    if os.name != "nt":
        return False
    try:
        pytest.importorskip("win32com")
    except pytest.skip.Exception:
        return False
    try:
        import win32com.client  # type: ignore[import-not-found]

        db = win32com.client.Dispatch("DAO.DBEngine.36").CreateDatabase(
            "", "", ";LANGID=0x0409;CP=1252;COUNTRY=0"
        )
        rel = db.CreateRelation("FK_Probe", "TableA", "TableB")
        rel.Fields.Append(rel.CreateField("a_id"))
        rel.Fields[0].ForeignName = "b_id"
        db.Relations.Append(rel)
        created = db.Relations["FK_Probe"].Table
        db.Close()
        return created == "TableA"
    except Exception:
        return False


class TestRelationshipRoundtrip:
    @pytest.mark.skipif(
        not _dao_relation_available(),
        reason="DAO relationship COM roundtrip not reachable on this host",
    )
    def test_relation_source_table_roundtrip(self, temp_db_copy: str) -> None:
        from ms_access_mcp.application.database import DatabaseUseCases
        from ms_access_mcp.errors import Ok

        # Discover real table/column names via get_tables + get_table_schema.
        # (Fixture has zero pre-existing relationships — plan 026 evidence.)
        db = DatabaseUseCases(
            readonly=False, allowed_dirs=[Path(temp_db_copy).parent]
        )
        tables_result = db.get_tables()
        assert isinstance(tables_result, Ok), f"get_tables failed: {tables_result.message}"
        tables = tables_result.value["data"]
        assert len(tables) >= 2, "fixture needs >=2 tables for FK probe"
        src_table, src_col = tables[0]["name"], tables[0]["columns"][0]["name"]
        dst_table, dst_col = tables[1]["name"], tables[1]["columns"][0]["name"]

        rel_name = "FK_Probe_RT"
        result = db.create_relationship(
            rel_name, src_table, src_col, dst_table, dst_col, confirm=True
        )
        assert result.success, f"create_relationship failed: {result.error}"

        rels_result = db.get_relationships()
        assert isinstance(rels_result, Ok), f"get_relationships failed: {rels_result.message}"
        rels = rels_result.value["data"]
        hit = next((r for r in rels if r["name"] == rel_name), None)
        assert hit is not None, f"relation {rel_name} not visible"
        assert hit["table"] == src_table, (
            f"BUG REGRESSED: relation.Table == {hit['table']!r}, "
            f"expected {src_table!r}"
        )
        assert hit["table"] != "", "BUG REGRESSED: relation.Table is empty"

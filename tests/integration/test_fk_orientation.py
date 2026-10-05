from __future__ import annotations

import os

import pytest

from tests.integration.conftest import _has_ace_dao

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows-only test"),
    pytest.mark.skipif(not _has_ace_dao(), reason="Requires ACE DAO"),
    pytest.mark.com_integration,
]


def test_dao_foreign_key_orientation_matches_schema_contract(tmp_path):
    """Prove DAO's relation orientation and check both schema readers."""
    from ms_access_mcp.backends.dao import DaoAdapter

    database_path = str(tmp_path / "fk_orientation.accdb")
    adapter = DaoAdapter(database_path)
    adapter.create_database(database_path)
    adapter.connect(database_path)
    try:
        assert adapter.create_table(
            "Customers",
            [{"name": "id", "type": "Long", "is_autoincrement": True}],
        )["success"]
        assert adapter.create_table(
            "Orders",
            [
                {"name": "id", "type": "Long", "is_autoincrement": True},
                {"name": "customer_id", "type": "Long"},
            ],
        )["success"]
        created = adapter.create_relationship(
            table_name="Customers",
            relationship_name="FK_Orders_Customers",
            columns=["id"],
            foreign_table="Orders",
            foreign_columns=["customer_id"],
        )
        assert created.success, created.error

        def inspect_relation():
            relation = adapter._dispatcher.current_db.Relations("FK_Orders_Customers")
            fields = [
                (relation.Fields(j).Name, relation.Fields(j).ForeignName)
                for j in range(relation.Fields.Count)
            ]
            return relation.Table, relation.ForeignTable, fields

        # All DAO COM reads occur on the adapter's STA dispatcher.
        table, foreign_table, fields = adapter._dispatcher.call(inspect_relation)
        print(
            "DAO FK ground truth:",
            {"Relation.Table": table, "Relation.ForeignTable": foreign_table, "Fields": fields},
        )
        assert (table, foreign_table, fields) == (
            "Customers",
            "Orders",
            [("id", "customer_id")],
        )

        relationships = adapter.get_relationships()
        assert len(relationships) == 1
        relationship = relationships[0]
        assert (relationship.table, relationship.foreign_table) == ("Orders", "Customers")
        assert (relationship.columns, relationship.foreign_columns) == (["customer_id"], ["id"])

        schemas, _ = adapter.get_table_schema_plan()
        by_name = {schema.name: schema for schema in schemas}
        assert by_name["Orders"].foreign_keys[0].columns == ["customer_id"]
        assert by_name["Orders"].foreign_keys[0].referenced_table == "Customers"
        assert by_name["Orders"].foreign_keys[0].referenced_columns == ["id"]
        assert by_name["Customers"].foreign_keys == []
    finally:
        adapter.disconnect()

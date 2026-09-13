"""Tests for schema-plan dataclasses in models.py."""

from __future__ import annotations

from ms_access_mcp.models import (
    ColumnSchema,
    ForeignKeySchema,
    IndexSchema,
    TableSchema,
    UnknownMetadata,
)


class TestColumnSchema:
    def test_name_stripped(self) -> None:
        c = ColumnSchema("  id  ", " Long Integer ")
        assert c.name == "id"
        assert c.source_type == "Long Integer"

    def test_source_type_stripped(self) -> None:
        c = ColumnSchema("name", "  Text  ")
        assert c.source_type == "Text"

    def test_default_value_empty_string_becomes_none(self) -> None:
        c = ColumnSchema("col", "Text", default_value="  ")
        assert c.default_value is None

    def test_default_value_whitespace_stripped(self) -> None:
        c = ColumnSchema("col", "Text", default_value="  somevalue  ")
        assert c.default_value == "somevalue"

    def test_default_value_none_preserved(self) -> None:
        c = ColumnSchema("col", "Text", default_value=None)
        assert c.default_value is None

    def test_fields_default_values(self) -> None:
        c = ColumnSchema("col", "Long Integer")
        assert c.max_length is None
        assert c.allow_null is True
        assert c.is_autoincrement is False
        assert c.default_value is None


class TestForeignKeySchema:
    def test_fields(self) -> None:
        fk = ForeignKeySchema(
            name="FK_Customers_Orders",
            columns=["customer_id"],
            referenced_table="Customers",
            referenced_columns=["id"],
        )
        assert fk.name == "FK_Customers_Orders"
        assert fk.columns == ["customer_id"]
        assert fk.referenced_table == "Customers"
        assert fk.referenced_columns == ["id"]


class TestIndexSchema:
    def test_fields(self) -> None:
        idx = IndexSchema(name="IX_Name", columns=["LastName", "FirstName"], is_unique=True)
        assert idx.name == "IX_Name"
        assert idx.columns == ["LastName", "FirstName"]
        assert idx.is_unique is True

    def test_is_unique_defaults_false(self) -> None:
        idx = IndexSchema(name="IX_Name", columns=["Name"])
        assert idx.is_unique is False


class TestUnknownMetadata:
    def test_all_false_by_default(self) -> None:
        u = UnknownMetadata()
        assert u.primary_keys is False
        assert u.foreign_keys is False
        assert u.defaults is False
        assert u.indexes is False
        assert u.autoincrement is False

    def test_can_set_individual_flags(self) -> None:
        u = UnknownMetadata(primary_keys=True, defaults=True)
        assert u.primary_keys is True
        assert u.defaults is True
        assert u.foreign_keys is False


class TestTableSchema:
    def test_name_stripped(self) -> None:
        t = TableSchema(
            "  Customers  ",
            columns=[ColumnSchema("id", "Long Integer")],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        assert t.name == "Customers"

    def test_columns_passed_through(self) -> None:
        cols = [ColumnSchema("id", "Long Integer"), ColumnSchema("name", "Text")]
        t = TableSchema("Customers", columns=cols, primary_key=["id"], foreign_keys=[], indexes=[])
        assert len(t.columns) == 2
        assert t.columns[0].name == "id"

    def test_primary_key_passed_through(self) -> None:
        t = TableSchema(
            "Customers",
            columns=[ColumnSchema("id", "Long Integer")],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[],
        )
        assert t.primary_key == ["id"]

    def test_foreign_keys_passed_through(self) -> None:
        fk = ForeignKeySchema(
            name="FK_Customers_Orders",
            columns=["customer_id"],
            referenced_table="Customers",
            referenced_columns=["id"],
        )
        t = TableSchema(
            "Orders",
            columns=[ColumnSchema("customer_id", "Long Integer")],
            primary_key=["id"],
            foreign_keys=[fk],
            indexes=[],
        )
        assert len(t.foreign_keys) == 1
        assert t.foreign_keys[0].name == "FK_Customers_Orders"

    def test_indexes_passed_through(self) -> None:
        idx = IndexSchema(name="IX_Name", columns=["LastName"])
        t = TableSchema(
            "Customers",
            columns=[ColumnSchema("LastName", "Text")],
            primary_key=["id"],
            foreign_keys=[],
            indexes=[idx],
        )
        assert len(t.indexes) == 1
        assert t.indexes[0].name == "IX_Name"

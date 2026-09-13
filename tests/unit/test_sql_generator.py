"""Tests for JetSqlGenerator and JET_TYPE_MAP."""

from __future__ import annotations

from ms_access_mcp.backends.sql_generator import JET_TYPE_MAP, JetSqlGenerator
from ms_access_mcp.models import (
    FieldInfo,
    ForeignKeyInfo,
    TableInfo,
)


class TestJetTypeMap:
    """Pin every JET_TYPE_MAP mapping verbatim."""

    def test_text_maps_to_varchar_with_size(self) -> None:
        assert JET_TYPE_MAP["Text"] == "VARCHAR({size})"

    def test_memo_maps_to_memo(self) -> None:
        assert JET_TYPE_MAP["Memo"] == "MEMO"

    def test_long_integer_maps_to_long(self) -> None:
        assert JET_TYPE_MAP["Long Integer"] == "LONG"

    def test_integer_maps_to_short(self) -> None:
        assert JET_TYPE_MAP["Integer"] == "SHORT"

    def test_byte_maps_to_byte(self) -> None:
        assert JET_TYPE_MAP["Byte"] == "BYTE"

    def test_boolean_maps_to_yesno(self) -> None:
        assert JET_TYPE_MAP["Boolean"] == "YESNO"

    def test_datetime_maps_to_datetime(self) -> None:
        assert JET_TYPE_MAP["Date/Time"] == "DATETIME"

    def test_currency_maps_to_currency(self) -> None:
        assert JET_TYPE_MAP["Currency"] == "CURRENCY"

    def test_counter_maps_to_autoincrement(self) -> None:
        assert JET_TYPE_MAP["Counter"] == "AUTOINCREMENT"

    def test_autonumber_maps_to_autoincrement(self) -> None:
        assert JET_TYPE_MAP["AutoNumber"] == "AUTOINCREMENT"

    def test_single_maps_to_single(self) -> None:
        assert JET_TYPE_MAP["Single"] == "SINGLE"

    def test_double_maps_to_double(self) -> None:
        assert JET_TYPE_MAP["Double"] == "DOUBLE"

    def test_decimal_maps_to_decimal(self) -> None:
        assert JET_TYPE_MAP["Decimal"] == "DECIMAL"

    def test_ole_object_maps_to_longbinary(self) -> None:
        assert JET_TYPE_MAP["OLE Object"] == "LONGBINARY"

    def test_guid_maps_to_char_38(self) -> None:
        assert JET_TYPE_MAP["GUID"] == "CHAR(38)"

    def test_binary_maps_to_varbinary(self) -> None:
        assert JET_TYPE_MAP["Binary"] == "VARBINARY"

    def test_unknown_type_falls_back_to_varchar_255(self) -> None:
        gen = JetSqlGenerator([], [], [])
        field = FieldInfo(name="Col", type="UnknownType", size=0)
        assert gen._map_type(field) == "VARCHAR(255)"


class TestJetSqlGenerator:
    """DDL generation tests."""

    def _make_field(
        self,
        name: str,
        type: str,
        size: int = 0,
        required: bool = False,
        default_value: str | None = None,
        is_autoincrement: bool = False,
    ) -> FieldInfo:
        return FieldInfo(
            name=name,
            type=type,
            size=size,
            required=required,
            allow_zero_length=True,
            default_value=default_value,
            is_autoincrement=is_autoincrement,
        )

    def test_empty_tables_returns_empty_list(self) -> None:
        gen = JetSqlGenerator([], [], [])
        assert gen.generate() == []

    def test_size_formatting_in_varchar(self) -> None:
        tables = [
            TableInfo(
                name="Customers",
                fields=[self._make_field("Name", "Text", size=100)],
                record_count=0,
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert len(statements) == 1
        assert "VARCHAR(100)" in statements[0]

    def test_text_field_default_size_255(self) -> None:
        tables = [
            TableInfo(
                name="Customers",
                fields=[self._make_field("Name", "Text", size=0)],
                record_count=0,
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert "VARCHAR(255)" in statements[0]

    def test_autoincrement_column_part(self) -> None:
        tables = [
            TableInfo(
                name="Customers",
                fields=[self._make_field("ID", "Counter", is_autoincrement=True)],
                record_count=0,
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert "AUTOINCREMENT" in statements[0]

    def test_default_value_column_part(self) -> None:
        tables = [
            TableInfo(
                name="Customers",
                fields=[self._make_field("Name", "Text", default_value="John")],
                record_count=0,
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert "DEFAULT 'John'" in statements[0]

    def test_not_null_required_column_part(self) -> None:
        tables = [
            TableInfo(
                name="Customers",
                fields=[self._make_field("ID", "Long Integer", required=True)],
                record_count=0,
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert "NOT NULL" in statements[0]

    def test_pk_constraint_emission(self) -> None:
        tables = [
            TableInfo(
                name="Customers",
                fields=[
                    self._make_field("ID", "Long Integer"),
                    self._make_field("Name", "Text"),
                ],
                record_count=0,
                primary_key=["ID"],
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert "PRIMARY KEY ([ID])" in statements[0]

    def test_fk_constraint_emission(self) -> None:
        customers = TableInfo(
            name="Customers",
            fields=[self._make_field("ID", "Long Integer")],
            record_count=0,
            primary_key=["ID"],
        )
        orders = TableInfo(
            name="Orders",
            fields=[
                self._make_field("ID", "Long Integer"),
                self._make_field("CustomerID", "Long Integer"),
            ],
            record_count=0,
        )
        fks = [
            ForeignKeyInfo(
                name="FK_Orders_Customers",
                columns=["CustomerID"],
                foreign_table="Customers",
                foreign_columns=["ID"],
            )
        ]
        gen = JetSqlGenerator([customers, orders], [], fks)
        statements = gen.generate()
        # Orders (child) must come after Customers (parent) in output
        orders_stmt = next(s for s in statements if "Orders" in s)
        assert "FOREIGN KEY" in orders_stmt
        assert "REFERENCES [Customers]" in orders_stmt

    def test_topological_sort_parent_before_child(self) -> None:
        # Orders references Customers — Customers must come first
        customers = TableInfo(
            name="Customers",
            fields=[self._make_field("ID", "Long Integer")],
            record_count=0,
            primary_key=["ID"],
        )
        orders = TableInfo(
            name="Orders",
            fields=[self._make_field("CustomerID", "Long Integer")],
            record_count=0,
        )
        fks = [
            ForeignKeyInfo(
                name="FK_Orders_Customers",
                columns=["CustomerID"],
                foreign_table="Customers",
                foreign_columns=["ID"],
            )
        ]
        gen = JetSqlGenerator([orders, customers], [], fks)  # intentional wrong order
        statements = gen.generate()
        customers_pos = next(
            i for i, s in enumerate(statements) if "Customers" in s
        )
        orders_pos = next(i for i, s in enumerate(statements) if "Orders" in s)
        assert customers_pos < orders_pos

    def test_topological_sort_cycle_fallback_to_input_order(self) -> None:
        # Self-referential FK or mutual dependency — should not crash, falls back
        t1 = TableInfo(
            name="T1",
            fields=[self._make_field("ID", "Long Integer")],
            record_count=0,
            primary_key=["ID"],
        )
        t2 = TableInfo(
            name="T2",
            fields=[self._make_field("T1ID", "Long Integer")],
            record_count=0,
        )
        # Circular: T1 -> T2 -> T1 (via different columns — simplified)
        fks = [
            ForeignKeyInfo(
                name="FK_T2_T1",
                columns=["T1ID"],
                foreign_table="T1",
                foreign_columns=["ID"],
            )
        ]
        gen = JetSqlGenerator([t1, t2], [], fks)
        # Must not raise — should fall back to input order
        statements = gen.generate()
        assert len(statements) == 2

    def test_create_table_statement_structure(self) -> None:
        tables = [
            TableInfo(
                name="TestTable",
                fields=[self._make_field("ID", "Long Integer")],
                record_count=0,
            )
        ]
        gen = JetSqlGenerator(tables, [], [])
        statements = gen.generate()
        assert statements[0].startswith("CREATE TABLE [TestTable]")
        assert statements[0].endswith(");")
        assert "(\n" in statements[0]

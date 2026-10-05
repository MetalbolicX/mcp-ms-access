"""Tests for SchemaInspector using fake COM dispatcher."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from ms_access_mcp.backends.schema_inspector import SchemaInspector


class FakeField:
    def __init__(
        self,
        name: str,
        type: int,
        size: int = 0,
        required: bool = False,
        allow_zero_length: bool = True,
        default_value: Any = None,
        attributes: int = 0,
    ) -> None:
        self.Name = name
        self.Type = type
        self.Size = size
        self.Required = required
        self.AllowZeroLength = allow_zero_length
        self.DefaultValue = default_value
        self.Attributes = attributes


class FakeFields:
    def __init__(self, fields: list[FakeField]) -> None:
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)

    def __getitem__(self, idx: int) -> FakeField:
        return self._fields[idx]

    def __len__(self) -> int:
        return len(self._fields)


class FakeIndex:
    def __init__(
        self,
        name: str,
        fields: list[FakeField],
        primary: bool = False,
        unique: bool = False,
        ignore_nulls: bool = False,
    ) -> None:
        self.Name = name
        self.Fields = FakeFields(fields)
        self.Primary = primary
        self.Unique = unique
        self.IgnoreNulls = ignore_nulls


class FakeIndexes:
    def __init__(self, indexes: list[FakeIndex]) -> None:
        self._indexes = indexes

    def __iter__(self):
        return iter(self._indexes)


class FakeTableDef:
    def __init__(
        self,
        name: str,
        fields: list[FakeField],
        indexes: list[FakeIndex] | None = None,
        attributes: int = 0,
    ) -> None:
        self.Name = name
        self.Fields = FakeFields(fields)
        self.Indexes = FakeIndexes(indexes) if indexes is not None else FakeIndexes([])
        self.Attributes = attributes


class FakeTableDefs:
    def __init__(self, tdefs: list[FakeTableDef]) -> None:
        self._tdefs = tdefs
        self.iter_calls = 0

    def __iter__(self):
        self.iter_calls += 1
        return iter(self._tdefs)

    def __getitem__(self, name: str) -> FakeTableDef:
        return next(table for table in self._tdefs if table.Name == name)


class FakeRelField:
    def __init__(self, name: str, foreign_name: str) -> None:
        self.Name = name
        self.ForeignName = foreign_name


class FakeRelFields:
    def __init__(self, fields: list[FakeRelField]) -> None:
        self._fields = fields

    def __iter__(self):
        return iter(self._fields)

    def __getitem__(self, idx: int) -> FakeRelField:
        return self._fields[idx]

    def __call__(self, idx: int) -> FakeRelField:
        """COM-style collection access: rel.Fields(j)"""
        return self._fields[idx]

    def Append(self, field: FakeRelField) -> None:
        """Append a field to this relation's Fields collection."""
        self._fields.append(field)

    def __len__(self) -> int:
        return len(self._fields)

    @property
    def Count(self) -> int:
        return len(self._fields)


class FakeRelation:
    def __init__(
        self,
        name: str,
        table: str,
        foreign_table: str,
        fields: list[FakeRelField],
        attributes: int = 0,
    ) -> None:
        self.Name = name
        self.Table = table
        self.ForeignTable = foreign_table
        self.Fields = FakeRelFields(fields)
        self.Attributes = attributes

    def CreateField(self, name: str) -> FakeRelField:
        """Create a new field on this relation."""
        field = FakeRelField(name=name, foreign_name="")
        return field


class FakeRelations:
    def __init__(self, rels: list[FakeRelation]) -> None:
        self._rels = rels

    def __iter__(self):
        return iter(self._rels)

    def Append(self, rel: FakeRelation) -> None:
        pass

    def Delete(self, name: str) -> None:
        pass


class FakeQueryDef:
    def __init__(self, name: str, sql: str = "") -> None:
        self.Name = name
        self.SQL = sql


class FakeQueryDefs:
    def __init__(self, qdefs: list[FakeQueryDef]) -> None:
        self._qdefs = qdefs

    def __iter__(self):
        return iter(self._qdefs)


class FakeRecordset:
    def __init__(self, count: int) -> None:
        self._count = count
        self.EOF = False

    def Close(self) -> None:
        pass

    @property
    def Fields(self) -> Any:
        count = self._count

        class FakeRSField:
            def __init__(self, val: int) -> None:
                self.Value = val

        class FakeRSFields:
            def __getitem__(self, idx: int) -> FakeRSField:
                return FakeRSField(count)

        return FakeRSFields()


class FakeDb:
    def __init__(
        self,
        tdefs: list[FakeTableDef],
        rels: list[FakeRelation] | None = None,
        qdefs: list[FakeQueryDef] | None = None,
        name: str = "test.accdb",
    ) -> None:
        self.TableDefs = FakeTableDefs(tdefs)
        self.Relations = FakeRelations(rels) if rels is not None else FakeRelations([])
        self.QueryDefs = FakeQueryDefs(qdefs) if qdefs is not None else FakeQueryDefs([])
        self.Name = name

    def OpenRecordset(self, sql: str) -> FakeRecordset:
        # Return count 5 for any SELECT COUNT
        return FakeRecordset(5)

    def CreateRelation(self, name: str, table: str, foreign_table: str) -> FakeRelation:
        return FakeRelation(name=name, table=table, foreign_table=foreign_table, fields=[])


class FakeDispatcher:
    def __init__(
        self,
        db: FakeDb | None = None,
        connected: bool = True,
        access_app: Any = None,
    ) -> None:
        self._db = db
        self._connected = connected
        self._access_app = access_app or MagicMock()

    def is_connected(self) -> bool:
        return self._connected

    @property
    def current_db(self) -> Any:
        return self._db

    @property
    def access_app(self) -> Any:
        return self._access_app

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if not self._connected:
            raise RuntimeError("Not connected")
        if self._db is None:
            raise RuntimeError("No db")
        return fn(*args, **kwargs)


class TestGetTables:
    def test_returns_tables_with_fields(self) -> None:
        tdef = FakeTableDef(
            "Customers",
            [
                FakeField("id", 4),  # Long Integer
                FakeField("Name", 10, size=100),  # Text
            ],
        )
        db = FakeDb(tdefs=[tdef])
        insp = SchemaInspector(FakeDispatcher(db=db))
        tables = insp.get_tables()
        assert len(tables) == 1
        assert tables[0].name == "Customers"
        assert len(tables[0].fields) == 2
        assert tables[0].fields[0].name == "id"
        assert tables[0].fields[0].type == "Long Integer"
        assert tables[0].fields[1].name == "Name"
        assert tables[0].fields[1].type == "Text"

    def test_skips_msys_tables(self) -> None:
        tdefs = [
            FakeTableDef("MSysUsers", [FakeField("id", 4)]),
            FakeTableDef("Customers", [FakeField("id", 4)]),
        ]
        db = FakeDb(tdefs=tdefs)
        insp = SchemaInspector(FakeDispatcher(db=db))
        tables = insp.get_tables()
        names = [t.name for t in tables]
        assert "MSysUsers" not in names
        assert "Customers" in names

    def test_skips_hidden_tables(self) -> None:
        tdefs = [
            FakeTableDef("Hidden", [FakeField("id", 4)], attributes=0x80000000),
            FakeTableDef("Visible", [FakeField("id", 4)]),
        ]
        db = FakeDb(tdefs=tdefs)
        insp = SchemaInspector(FakeDispatcher(db=db))
        tables = insp.get_tables()
        names = [t.name for t in tables]
        assert "Hidden" not in names
        assert "Visible" in names

    def test_skips_tilde_tables(self) -> None:
        tdefs = [
            FakeTableDef("~tmp", [FakeField("id", 4)]),
            FakeTableDef("Customers", [FakeField("id", 4)]),
        ]
        db = FakeDb(tdefs=tdefs)
        insp = SchemaInspector(FakeDispatcher(db=db))
        tables = insp.get_tables()
        names = [t.name for t in tables]
        assert "~tmp" not in names

    def test_disconnected_returns_empty(self) -> None:
        insp = SchemaInspector(FakeDispatcher(db=None, connected=False))
        assert insp.get_tables() == []

    def test_get_tables_does_not_query_row_counts(self) -> None:
        tdef = FakeTableDef("Customers", [FakeField("id", 4)])
        db = FakeDb(tdefs=[tdef])
        db.OpenRecordset = MagicMock(side_effect=AssertionError("unexpected row-count query"))
        insp = SchemaInspector(FakeDispatcher(db=db))

        tables = insp.get_tables()

        assert [table.name for table in tables] == ["Customers"]
        db.OpenRecordset.assert_not_called()


class TestGetSystemTables:
    def test_returns_msys_tables_with_zero_record_count(self) -> None:
        tdef = FakeTableDef("MSysObjects", [FakeField("id", 4)])
        db = FakeDb(tdefs=[tdef])
        insp = SchemaInspector(FakeDispatcher(db=db))
        tables = insp.get_system_tables()
        assert len(tables) == 1
        assert tables[0].name == "MSysObjects"
        assert tables[0].record_count == 0


class TestGetQueries:
    def test_returns_query_defs(self) -> None:
        qdefs = [FakeQueryDef("qry1", "SELECT 1"), FakeQueryDef("qry2", "SELECT 2")]
        db = FakeDb(tdefs=[], qdefs=qdefs)
        insp = SchemaInspector(FakeDispatcher(db=db))
        rels = insp.get_queries()
        assert len(rels) == 2
        assert rels[0].name == "qry1"
        assert rels[1].name == "qry2"


class TestGetIndexes:
    def test_returns_indexes_with_flags(self) -> None:
        id_field = FakeField("id", 4)
        name_field = FakeField("Name", 10)
        tdef = FakeTableDef(
            "Customers",
            [id_field, name_field],
            indexes=[
                FakeIndex("PK_Customers", [id_field], primary=True),
                FakeIndex("IX_Name", [name_field], unique=True),
            ],
        )
        db = FakeDb(tdefs=[tdef])
        insp = SchemaInspector(FakeDispatcher(db=db))
        indexes = insp.get_indexes("Customers")
        assert db.TableDefs.iter_calls == 0
        assert len(indexes) == 2
        pk = next(i for i in indexes if i.is_primary)
        assert pk.name == "PK_Customers"
        assert pk.columns == ["id"]
        ix = next(i for i in indexes if not i.is_primary)
        assert ix.name == "IX_Name"
        assert ix.is_unique is True

    def test_skips_column_less_indexes(self) -> None:
        tdef = FakeTableDef("Customers", [FakeField("id", 4)], indexes=[])
        db = FakeDb(tdefs=[tdef])
        insp = SchemaInspector(FakeDispatcher(db=db))
        indexes = insp.get_indexes("Customers")
        assert db.TableDefs.iter_calls == 0
        assert indexes == []


class TestGetRelationships:
    def test_returns_relationships(self) -> None:
        rel = FakeRelation(
            name="FK_Customers_Orders",
            table="Customers",
            foreign_table="Orders",
            fields=[FakeRelField("id", "customer_id")],
        )
        db = FakeDb(tdefs=[], rels=[rel])
        insp = SchemaInspector(FakeDispatcher(db=db))
        rels_out = insp.get_relationships()
        assert len(rels_out) == 1
        assert rels_out[0].name == "FK_Customers_Orders"
        assert rels_out[0].table == "Orders"  # child
        assert rels_out[0].foreign_table == "Customers"  # parent
        assert rels_out[0].columns == ["customer_id"]
        assert rels_out[0].foreign_columns == ["id"]

    def test_skips_msys_relationships(self) -> None:
        rels = [
            FakeRelation("MSys_FK", "A", "B", [FakeRelField("a", "b")]),
            FakeRelation("FK_Real", "A", "B", [FakeRelField("a", "b")]),
        ]
        db = FakeDb(tdefs=[], rels=rels)
        insp = SchemaInspector(FakeDispatcher(db=db))
        result = insp.get_relationships()
        names = [r.name for r in result]
        assert "MSys_FK" not in names
        assert "FK_Real" in names


class TestGetTableSchemaPlan:
    def test_attaches_foreign_key_to_child_table(self) -> None:
        customer_id = FakeField("id", 4, attributes=0x10)
        order_id = FakeField("id", 4, attributes=0x10)
        customer_fk = FakeField("customer_id", 4)
        tables = [
            FakeTableDef("Customers", [customer_id], [FakeIndex("PK_Customers", [customer_id], primary=True)]),
            FakeTableDef("Orders", [order_id, customer_fk], [FakeIndex("PK_Orders", [order_id], primary=True)]),
        ]
        relation = FakeRelation(
            "FK_Orders_Customers",
            "Customers",
            "Orders",
            [FakeRelField("id", "customer_id")],
        )
        inspector = SchemaInspector(FakeDispatcher(db=FakeDb(tdefs=tables, rels=[relation])))

        schemas, _ = inspector.get_table_schema_plan()
        by_name = {schema.name: schema for schema in schemas}

        assert by_name["Orders"].foreign_keys[0].columns == ["customer_id"]
        assert by_name["Orders"].foreign_keys[0].referenced_table == "Customers"
        assert by_name["Orders"].foreign_keys[0].referenced_columns == ["id"]
        assert by_name["Customers"].foreign_keys == []

    def test_returns_table_schemas(self) -> None:
        id_field = FakeField("id", 4, attributes=0x10)  # autoincrement
        name_field = FakeField("Name", 10, size=100, required=True)
        tdef = FakeTableDef(
            "Customers",
            [id_field, name_field],
            indexes=[FakeIndex("PK_Customers", [id_field], primary=True)],
        )
        db = FakeDb(tdefs=[tdef])
        insp = SchemaInspector(FakeDispatcher(db=db))
        schemas, unknown = insp.get_table_schema_plan()
        assert len(schemas) == 1
        assert schemas[0].name == "Customers"
        assert schemas[0].primary_key == ["id"]
        assert len(schemas[0].columns) == 2
        assert schemas[0].columns[0].name == "id"
        assert schemas[0].columns[0].is_autoincrement is True
        assert schemas[0].columns[1].name == "Name"
        assert schemas[0].columns[1].allow_null is False
        assert schemas[0].columns[1].max_length == 100
        assert unknown.autoincrement is True

    def test_unknown_flags_set_when_data_missing(self) -> None:
        # Table with no PK index
        tdef = FakeTableDef("Customers", [FakeField("Name", 10)], indexes=[])
        db = FakeDb(tdefs=[tdef])
        insp = SchemaInspector(FakeDispatcher(db=db))
        _, unknown = insp.get_table_schema_plan()
        assert unknown.primary_keys is True


class TestCreateRelationship:
    def test_creates_relationship(self) -> None:
        db = FakeDb(tdefs=[])
        created: list[str] = []

        class TrackingRelations(FakeRelations):
            def Append(self, rel: FakeRelation) -> None:
                created.append(rel.Name)

        db.Relations = TrackingRelations([])

        dispatcher = FakeDispatcher(db=db)
        insp = SchemaInspector(dispatcher)
        result = insp.create_relationship(
            table_name="Customers",
            relationship_name="FK_Customers_Orders",
            columns=["id"],
            foreign_table="Orders",
            foreign_columns=["customer_id"],
        )
        assert result.success is True, f"Expected success, got error: {result.error}"
        assert "FK_Customers_Orders" in created


class TestDeleteRelationship:
    def test_deletes_relationship(self) -> None:
        deleted: list[str] = []

        class TrackingRelations(FakeRelations):
            def Delete(self, name: str) -> None:
                deleted.append(name)

        db = FakeDb(tdefs=[])
        db.Relations = TrackingRelations([])

        dispatcher = FakeDispatcher(db=db)
        insp = SchemaInspector(dispatcher)
        result = insp.delete_relationship("Customers", "FK_Customers_Orders")
        assert result.success is True
        assert "FK_Customers_Orders" in deleted


class TestGetObjectMetadata:
    def test_returns_table_metadata(self) -> None:
        tdef = FakeTableDef("Customers", [FakeField("id", 4)])
        db = FakeDb(tdefs=[tdef])
        db.OpenRecordset = MagicMock(side_effect=AssertionError("unexpected row-count query"))
        dispatcher = FakeDispatcher(db=db)
        insp = SchemaInspector(dispatcher)
        meta = insp.get_object_metadata("Customers")
        assert meta is not None
        assert db.TableDefs.iter_calls == 0
        assert meta.name == "Customers"
        assert meta.type == "table"
        assert meta.properties == {}
        db.OpenRecordset = MagicMock(side_effect=AssertionError("unexpected row-count query"))
        assert insp.get_object_metadata("Customers") == meta
        db.OpenRecordset.assert_not_called()


class TestGetDatabaseStatistics:
    def test_returns_statistics(self) -> None:
        db = FakeDb(tdefs=[])
        dispatcher = FakeDispatcher(db=db)
        insp = SchemaInspector(dispatcher)
        stats = insp.get_database_statistics()
        assert stats.success is True
        assert "tables" in stats.objects
        assert "name" in stats.file
        assert "access_version" in stats.system

    def test_disconnected_returns_zeroed(self) -> None:
        dispatcher = FakeDispatcher(db=None, connected=False)
        insp = SchemaInspector(dispatcher)
        stats = insp.get_database_statistics()
        assert stats.success is True
        assert stats.system["com_available"] is False
        assert stats.objects["tables"] == 0
        assert stats.objects["queries"] == 0
        assert stats.objects["forms"] == 0
        assert stats.objects["reports"] == 0
        assert stats.objects["macros"] == 0
        assert stats.objects["modules"] == 0
        assert stats.file["name"] == ""
        assert stats.file["size_bytes"] == 0
        assert stats.file["modified"] == ""


class TestGenerateSql:
    """Tests for generate_sql using fake dispatcher."""

    def test_generate_sql_writes_file_and_returns_success(
        self, tmp_path: Any
    ) -> None:
        from pathlib import Path

        id_field = FakeField("id", 4)  # Long Integer
        name_field = FakeField("Name", 10, size=100, required=True)
        pk_index = FakeIndex("PK_Customers", [id_field], primary=True)
        tdef = FakeTableDef(
            "Customers",
            [id_field, name_field],
            indexes=[pk_index],
        )
        db = FakeDb(tdefs=[tdef])
        dispatcher = FakeDispatcher(db=db)
        insp = SchemaInspector(dispatcher)

        output_path = tmp_path / "output.sql"
        result = insp.generate_sql(str(output_path))

        assert result.success is True
        assert result.path == str(output_path)
        assert result.statements >= 1
        assert result.tables == 1

        content = Path(str(output_path)).read_text(encoding="utf-8")
        assert "CREATE TABLE [Customers]" in content
        assert "[id]" in content
        assert "[Name]" in content
        assert "PRIMARY KEY" in content

    def test_generate_sql_uses_child_and_parent_fk_orientation(self, tmp_path: Any) -> None:
        from pathlib import Path

        customer_id = FakeField("id", 4)
        order_id = FakeField("id", 4)
        customer_fk = FakeField("customer_id", 4)
        tables = [
            FakeTableDef("Customers", [customer_id], [FakeIndex("PK_Customers", [customer_id], primary=True)]),
            FakeTableDef("Orders", [order_id, customer_fk], [FakeIndex("PK_Orders", [order_id], primary=True)]),
        ]
        relation = FakeRelation(
            "FK_Orders_Customers",
            "Customers",
            "Orders",
            [FakeRelField("id", "customer_id")],
        )
        inspector = SchemaInspector(FakeDispatcher(db=FakeDb(tdefs=tables, rels=[relation])))
        output_path = tmp_path / "foreign_keys.sql"

        result = inspector.generate_sql(str(output_path))

        assert result.success is True
        content = Path(output_path).read_text(encoding="utf-8")
        assert "FOREIGN KEY ([customer_id]) REFERENCES [Customers]([id])" in content

    def test_generate_sql_disconnected_returns_error(self) -> None:
        dispatcher = FakeDispatcher(db=None, connected=False)
        insp = SchemaInspector(dispatcher)
        result = insp.generate_sql("/tmp/out.sql")
        assert result.success is False
        assert result.error is not None
        assert "Not connected" in result.error

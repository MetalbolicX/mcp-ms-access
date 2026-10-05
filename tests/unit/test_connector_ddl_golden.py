"""Golden characterization tests for per-connector DDL rendering.

These pins prove byte-identical output is preserved across the DialectSpec
refactor. Each (dialect × fixture) combination is pinned as an exact string.
"""

from __future__ import annotations

import pytest

from ms_access_mcp.backends.migration.connectors import mysql, postgres, sqlite, sqlserver
from ms_access_mcp.models import ColumnSchema, ForeignKeySchema, IndexSchema, TableSchema

FK: list[ForeignKeySchema] = []
IDX: list[IndexSchema] = []


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def simple() -> TableSchema:
    """3-column table, no primary key."""
    return TableSchema(
        name="TestTable",
        columns=[
            ColumnSchema(name="ID", source_type="Long Integer", max_length=None,
                        allow_null=False, is_autoincrement=False, default_value=None),
            ColumnSchema(name="Name", source_type="Text", max_length=100,
                        allow_null=True, is_autoincrement=False, default_value=None),
            ColumnSchema(name="Created", source_type="Date/Time", max_length=None,
                        allow_null=True, is_autoincrement=False, default_value=None),
        ],
        primary_key=[],
        foreign_keys=FK,
        indexes=IDX,
    )


@pytest.fixture
def autoinc_pk() -> TableSchema:
    """Single Long Integer autoincrement column IS the sole primary key."""
    return TableSchema(
        name="AutoTable",
        columns=[
            ColumnSchema(name="ID", source_type="Long Integer", max_length=None,
                        allow_null=False, is_autoincrement=True, default_value=None),
            ColumnSchema(name="Name", source_type="Text", max_length=50,
                        allow_null=True, is_autoincrement=False, default_value=None),
        ],
        primary_key=["ID"],
        foreign_keys=FK,
        indexes=IDX,
    )


@pytest.fixture
def composite_pk() -> TableSchema:
    """Two-column primary key."""
    return TableSchema(
        name="LinkTable",
        columns=[
            ColumnSchema(name="LeftID", source_type="Long Integer", max_length=None,
                        allow_null=False, is_autoincrement=False, default_value=None),
            ColumnSchema(name="RightID", source_type="Long Integer", max_length=None,
                        allow_null=False, is_autoincrement=False, default_value=None),
            ColumnSchema(name="Relation", source_type="Text", max_length=20,
                        allow_null=True, is_autoincrement=False, default_value=None),
        ],
        primary_key=["LeftID", "RightID"],
        foreign_keys=FK,
        indexes=IDX,
    )


@pytest.fixture
def text_maxlen() -> TableSchema:
    """Text column with max_length set."""
    return TableSchema(
        name="TextTable",
        columns=[
            ColumnSchema(name="Code", source_type="Text", max_length=10,
                        allow_null=False, is_autoincrement=False, default_value=None),
            ColumnSchema(name="Desc", source_type="Text", max_length=255,
                        allow_null=True, is_autoincrement=False, default_value="DefaultDesc"),
        ],
        primary_key=[],
        foreign_keys=FK,
        indexes=IDX,
    )


@pytest.fixture
def binary_maxlen() -> TableSchema:
    """Binary column with max_length set."""
    return TableSchema(
        name="BinTable",
        columns=[
            ColumnSchema(name="Data", source_type="Binary", max_length=4096,
                        allow_null=True, is_autoincrement=False, default_value=None),
        ],
        primary_key=[],
        foreign_keys=FK,
        indexes=IDX,
    )


@pytest.fixture
def with_default() -> TableSchema:
    """Nullable columns with string and numeric default values."""
    return TableSchema(
        name="DefaultsTable",
        columns=[
            ColumnSchema(name="ID", source_type="Long Integer", max_length=None,
                        allow_null=False, is_autoincrement=False, default_value=None),
            ColumnSchema(name="Status", source_type="Text", max_length=20,
                        allow_null=True, is_autoincrement=False, default_value="Active"),
            ColumnSchema(name="Count", source_type="Integer", max_length=None,
                        allow_null=True, is_autoincrement=False, default_value="0"),
        ],
        primary_key=[],
        foreign_keys=FK,
        indexes=IDX,
    )


# ─── SQLite goldens ─────────────────────────────────────────────────────────

CREATE_TABLE_SQLITE_SIMPLE = (
    'CREATE TABLE "TestTable" (\n'
    '    "ID" INTEGER NOT NULL,\n'
    '    "Name" TEXT,\n'
    '    "Created" TEXT\n'
    ')'
)

CREATE_TABLE_SQLITE_AUTOINC_PK = (
    'CREATE TABLE "AutoTable" (\n'
    '    "ID" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,\n'
    '    "Name" TEXT\n'
    ')'
)

CREATE_TABLE_SQLITE_COMPOSITE_PK = (
    'CREATE TABLE "LinkTable" (\n'
    '    "LeftID" INTEGER NOT NULL,\n'
    '    "RightID" INTEGER NOT NULL,\n'
    '    "Relation" TEXT,\n'
    '    PRIMARY KEY ("LeftID", "RightID")\n'
    ')'
)

CREATE_TABLE_SQLITE_TEXT_MAXLEN = (
    'CREATE TABLE "TextTable" (\n'
    '    "Code" TEXT NOT NULL,\n'
    '    "Desc" TEXT DEFAULT DefaultDesc\n'
    ')'
)

CREATE_TABLE_SQLITE_BINARY_MAXLEN = (
    'CREATE TABLE "BinTable" (\n'
    '    "Data" BLOB\n'
    ')'
)

CREATE_TABLE_SQLITE_WITH_DEFAULT = (
    'CREATE TABLE "DefaultsTable" (\n'
    '    "ID" INTEGER NOT NULL,\n'
    '    "Status" TEXT DEFAULT Active,\n'
    '    "Count" INTEGER DEFAULT 0\n'
    ')'
)


# ─── PostgreSQL goldens ─────────────────────────────────────────────────────

CREATE_TABLE_POSTGRES_SIMPLE = (
    'CREATE TABLE "TestTable" (\n'
    '    "ID" INTEGER NOT NULL,\n'
    '    "Name" VARCHAR(100),\n'
    '    "Created" TIMESTAMP\n'
    ')'
)

CREATE_TABLE_POSTGRES_AUTOINC_PK = (
    'CREATE TABLE "AutoTable" (\n'
    '    "ID" INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY,\n'
    '    "Name" VARCHAR(50),\n'
    '    PRIMARY KEY ("ID")\n'
    ')'
)

CREATE_TABLE_POSTGRES_COMPOSITE_PK = (
    'CREATE TABLE "LinkTable" (\n'
    '    "LeftID" INTEGER NOT NULL,\n'
    '    "RightID" INTEGER NOT NULL,\n'
    '    "Relation" VARCHAR(20),\n'
    '    PRIMARY KEY ("LeftID", "RightID")\n'
    ')'
)

CREATE_TABLE_POSTGRES_TEXT_MAXLEN = (
    'CREATE TABLE "TextTable" (\n'
    '    "Code" VARCHAR(10) NOT NULL,\n'
    '    "Desc" VARCHAR(255) DEFAULT DefaultDesc\n'
    ')'
)

CREATE_TABLE_POSTGRES_BINARY_MAXLEN = (
    'CREATE TABLE "BinTable" (\n'
    '    "Data" BYTEA\n'
    ')'
)

CREATE_TABLE_POSTGRES_WITH_DEFAULT = (
    'CREATE TABLE "DefaultsTable" (\n'
    '    "ID" INTEGER NOT NULL,\n'
    '    "Status" VARCHAR(20) DEFAULT Active,\n'
    '    "Count" SMALLINT DEFAULT 0\n'
    ')'
)


# ─── MySQL goldens ───────────────────────────────────────────────────────────

CREATE_TABLE_MYSQL_SIMPLE = (
    'CREATE TABLE `TestTable` (\n'
    '    `ID` INT NOT NULL,\n'
    '    `Name` VARCHAR(100),\n'
    '    `Created` DATETIME\n'
    ')'
)

CREATE_TABLE_MYSQL_AUTOINC_PK = (
    'CREATE TABLE `AutoTable` (\n'
    '    `ID` INT NOT NULL AUTO_INCREMENT,\n'
    '    `Name` VARCHAR(50),\n'
    '    PRIMARY KEY (`ID`)\n'
    ')'
)

CREATE_TABLE_MYSQL_COMPOSITE_PK = (
    'CREATE TABLE `LinkTable` (\n'
    '    `LeftID` INT NOT NULL,\n'
    '    `RightID` INT NOT NULL,\n'
    '    `Relation` VARCHAR(20),\n'
    '    PRIMARY KEY (`LeftID`, `RightID`)\n'
    ')'
)

CREATE_TABLE_MYSQL_TEXT_MAXLEN = (
    'CREATE TABLE `TextTable` (\n'
    '    `Code` VARCHAR(10) NOT NULL,\n'
    '    `Desc` VARCHAR(255) DEFAULT DefaultDesc\n'
    ')'
)

CREATE_TABLE_MYSQL_BINARY_MAXLEN = (
    'CREATE TABLE `BinTable` (\n'
    '    `Data` VARBINARY(4096)\n'
    ')'
)

CREATE_TABLE_MYSQL_WITH_DEFAULT = (
    'CREATE TABLE `DefaultsTable` (\n'
    '    `ID` INT NOT NULL,\n'
    '    `Status` VARCHAR(20) DEFAULT Active,\n'
    '    `Count` SMALLINT DEFAULT 0\n'
    ')'
)


# ─── SQL Server goldens ─────────────────────────────────────────────────────

CREATE_TABLE_SQLSERVER_SIMPLE = (
    'CREATE TABLE [TestTable] (\n'
    '    [ID] INT NOT NULL,\n'
    '    [Name] NVARCHAR(100),\n'
    '    [Created] DATETIME2\n'
    ')'
)

CREATE_TABLE_SQLSERVER_AUTOINC_PK = (
    'CREATE TABLE [AutoTable] (\n'
    '    [ID] INT NOT NULL IDENTITY(1,1),\n'
    '    [Name] NVARCHAR(50),\n'
    '    PRIMARY KEY ([ID])\n'
    ')'
)

CREATE_TABLE_SQLSERVER_COMPOSITE_PK = (
    'CREATE TABLE [LinkTable] (\n'
    '    [LeftID] INT NOT NULL,\n'
    '    [RightID] INT NOT NULL,\n'
    '    [Relation] NVARCHAR(20),\n'
    '    PRIMARY KEY ([LeftID], [RightID])\n'
    ')'
)

CREATE_TABLE_SQLSERVER_TEXT_MAXLEN = (
    'CREATE TABLE [TextTable] (\n'
    '    [Code] NVARCHAR(10) NOT NULL,\n'
    '    [Desc] NVARCHAR(255) DEFAULT DefaultDesc\n'
    ')'
)

CREATE_TABLE_SQLSERVER_BINARY_MAXLEN = (
    'CREATE TABLE [BinTable] (\n'
    '    [Data] VARBINARY(4096)\n'
    ')'
)

CREATE_TABLE_SQLSERVER_WITH_DEFAULT = (
    'CREATE TABLE [DefaultsTable] (\n'
    '    [ID] INT NOT NULL,\n'
    '    [Status] NVARCHAR(20) DEFAULT Active,\n'
    '    [Count] SMALLINT DEFAULT 0\n'
    ')'
)


# ─── Parametric golden tests ─────────────────────────────────────────────────


def test_sqlite_foreign_key_is_golden_inline_and_has_no_alter_statement():
    from ms_access_mcp.backends.migration import render_schema_ddl

    parent = TableSchema(
        name="Customers",
        columns=[ColumnSchema(name="id", source_type="Long Integer", allow_null=False)],
        primary_key=["id"],
        foreign_keys=[],
        indexes=[],
    )
    child = TableSchema(
        name="Orders",
        columns=[
            ColumnSchema(name="id", source_type="Long Integer", allow_null=False),
            ColumnSchema(name="customer_id", source_type="Long Integer", allow_null=False),
        ],
        primary_key=["id"],
        foreign_keys=[
            ForeignKeySchema(
                name="FK_Orders_Customers",
                columns=["customer_id"],
                referenced_table="Customers",
                referenced_columns=["id"],
            )
        ],
        indexes=[],
    )

    expected_child = (
        'CREATE TABLE "Orders" (\n'
        '    "id" INTEGER NOT NULL,\n'
        '    "customer_id" INTEGER NOT NULL,\n'
        '    PRIMARY KEY ("id"),\n'
        '    FOREIGN KEY ("customer_id") REFERENCES "Customers"("id")\n'
        ')'
    )
    assert sqlite.render_table_ddl(child) == expected_child
    statements = render_schema_ddl("sqlite", [child, parent])
    assert statements == [
        'CREATE TABLE "Customers" (\n    "id" INTEGER NOT NULL,\n    PRIMARY KEY ("id")\n)',
        expected_child,
    ]
    assert all("ALTER TABLE" not in statement for statement in statements)


@pytest.mark.parametrize(
    ("dialect", "fixture", "expected"),
    [
        # SQLite
        ("sqlite", "simple", CREATE_TABLE_SQLITE_SIMPLE),
        ("sqlite", "autoinc_pk", CREATE_TABLE_SQLITE_AUTOINC_PK),
        ("sqlite", "composite_pk", CREATE_TABLE_SQLITE_COMPOSITE_PK),
        ("sqlite", "text_maxlen", CREATE_TABLE_SQLITE_TEXT_MAXLEN),
        ("sqlite", "binary_maxlen", CREATE_TABLE_SQLITE_BINARY_MAXLEN),
        ("sqlite", "with_default", CREATE_TABLE_SQLITE_WITH_DEFAULT),
        # PostgreSQL
        ("postgres", "simple", CREATE_TABLE_POSTGRES_SIMPLE),
        ("postgres", "autoinc_pk", CREATE_TABLE_POSTGRES_AUTOINC_PK),
        ("postgres", "composite_pk", CREATE_TABLE_POSTGRES_COMPOSITE_PK),
        ("postgres", "text_maxlen", CREATE_TABLE_POSTGRES_TEXT_MAXLEN),
        ("postgres", "binary_maxlen", CREATE_TABLE_POSTGRES_BINARY_MAXLEN),
        ("postgres", "with_default", CREATE_TABLE_POSTGRES_WITH_DEFAULT),
        # MySQL
        ("mysql", "simple", CREATE_TABLE_MYSQL_SIMPLE),
        ("mysql", "autoinc_pk", CREATE_TABLE_MYSQL_AUTOINC_PK),
        ("mysql", "composite_pk", CREATE_TABLE_MYSQL_COMPOSITE_PK),
        ("mysql", "text_maxlen", CREATE_TABLE_MYSQL_TEXT_MAXLEN),
        ("mysql", "binary_maxlen", CREATE_TABLE_MYSQL_BINARY_MAXLEN),
        ("mysql", "with_default", CREATE_TABLE_MYSQL_WITH_DEFAULT),
        # SQL Server
        ("sqlserver", "simple", CREATE_TABLE_SQLSERVER_SIMPLE),
        ("sqlserver", "autoinc_pk", CREATE_TABLE_SQLSERVER_AUTOINC_PK),
        ("sqlserver", "composite_pk", CREATE_TABLE_SQLSERVER_COMPOSITE_PK),
        ("sqlserver", "text_maxlen", CREATE_TABLE_SQLSERVER_TEXT_MAXLEN),
        ("sqlserver", "binary_maxlen", CREATE_TABLE_SQLSERVER_BINARY_MAXLEN),
        ("sqlserver", "with_default", CREATE_TABLE_SQLSERVER_WITH_DEFAULT),
    ],
)
def test_golden_ddl(
    request: pytest.FixtureRequest,
    dialect: str,
    fixture: str,
    expected: str,
) -> None:
    """Pin byte-exact DDL output per (dialect × fixture)."""
    f = request.getfixturevalue(fixture)
    mod = {"sqlite": sqlite, "postgres": postgres, "mysql": mysql, "sqlserver": sqlserver}[dialect]
    assert mod.render_table_ddl(f) == expected

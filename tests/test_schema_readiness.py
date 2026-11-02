"""Read-only startup schema checks using a mocked inspector."""

from unittest.mock import Mock

import pytest

from app.core import database
from app.db import models  # noqa: F401


@pytest.fixture
def schema_inspector(monkeypatch):
    columns = {
        table.name: [{"name": column.name} for column in table.columns]
        for table in database.Base.metadata.tables.values()
    }

    inspector = Mock(spec=["get_table_names", "get_columns"])
    inspector.get_table_names.return_value = list(columns)
    inspector.get_columns.side_effect = lambda name: columns[name]

    monkeypatch.setattr(database, "inspect", lambda engine: inspector)

    return inspector, columns


def test_complete_schema_passes(schema_inspector):
    inspector, columns = schema_inspector

    database.check_schema()

    inspector.get_table_names.assert_called_once()
    assert {
        call.args[0]
        for call in inspector.get_columns.call_args_list
    } == set(columns)


def test_missing_table_stops_before_column_inspection(schema_inspector):
    inspector, columns = schema_inspector
    inspector.get_table_names.return_value = [
        name for name in columns if name != "prediction_sessions"
    ]

    with pytest.raises(RuntimeError, match="missing tables") as caught:
        database.check_schema()

    assert "prediction_sessions" in str(caught.value)
    assert "alembic upgrade head" in str(caught.value)
    inspector.get_columns.assert_not_called()


def test_missing_assessment_column_is_detected(schema_inspector):
    _, columns = schema_inspector
    columns["prediction_sessions"] = [
        column
        for column in columns["prediction_sessions"]
        if column["name"] != "assessment"
    ]

    with pytest.raises(RuntimeError, match="missing columns") as caught:
        database.check_schema()

    assert "prediction_sessions.assessment" in str(caught.value)
    assert "alembic upgrade head" in str(caught.value)


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("users", "email"),
        ("model_versions", "is_active"),
    ],
)
def test_other_required_columns_are_checked(
    schema_inspector, table_name, column_name
):
    _, columns = schema_inspector
    columns[table_name] = [
        column
        for column in columns[table_name]
        if column["name"] != column_name
    ]

    with pytest.raises(RuntimeError, match="missing columns") as caught:
        database.check_schema()

    assert f"{table_name}.{column_name}" in str(caught.value)


def test_additional_tables_and_columns_are_allowed(schema_inspector):
    inspector, columns = schema_inspector
    inspector.get_table_names.return_value = [
        *columns,
        "additional_table",
    ]
    columns["prediction_sessions"].append(
        {"name": "additional_column"}
    )

    database.check_schema()

    assert "additional_table" not in {
        call.args[0]
        for call in inspector.get_columns.call_args_list
    }
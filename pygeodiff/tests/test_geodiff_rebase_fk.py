import os
from pathlib import Path
import re
import sqlite3
from typing import Tuple

import pytest

import pygeodiff
from pygeodiff import GeoDiffLibError

GEODIFFLIB = os.environ.get("GEODIFFLIB", None)


@pytest.mark.parametrize(
    "fk_constrained",
    [
        pytest.param(False, id="no_fk_constraint"),
        pytest.param(
            True,
            marks=pytest.mark.xfail(
                raises=GeoDiffLibError,
                reason="Expected to fail due to issue 210, when this xpasses remove this decorator",
            ),
            id="fkconstraint",
        ),
    ],
)
@pytest.mark.parametrize(
    "user_a_data_first", [True, False], ids=["user_a_data_first", "user_b_data_first"]
)
def test_geodiff_rebase_fk_happy_path(fk_constrained, user_a_data_first, tmp_path):
    """
    This test checks that rebase succeeds on simple changes on tables with
    foreign key constraints.  This test applies INSERT, UPDATE and DELETE
    changes that should not produce any conflicts.
    """
    # Arrange
    geodiff = pygeodiff.GeoDiff(GEODIFFLIB)
    conflict = tmp_path / "conflict.txt"
    original, user_a, user_b = create_gpkg_files(tmp_path, fk_constrained=False)

    # Apply changes to databases to give the following expected values
    expected_parents = set(['p1_updated', 'p2', 'p3', 'p4'])  # One edited, two added
    expected_children = set(['p1_child2', 'p2_child1', 'p2_child2', 'p3_child1'])  # One added, one deleted

    with sqlite3.connect(user_a) as conn_a, sqlite3.connect(user_b) as conn_b:
        conn_a.execute(
            """
            INSERT INTO parent VALUES (
                null,
                '6545d3eb-1cbd-45c6-9121-6758a6749866',
                'p3'
            )"""
        )
        conn_a.execute(
            """
            INSERT INTO child VALUES (
                null,
                '7d3cc6fc-f8f0-46b9-9f58-e6b9029a632e',
                '6545d3eb-1cbd-45c6-9121-6758a6749866',
                'p3_child1'
            )"""
        )
        conn_a.execute(
            """
            UPDATE parent SET name='p1_updated' WHERE name='p1'
            """
        )
        conn_b.execute(
            """
            INSERT INTO parent VALUES (
                null,
                'bec8d2d1-454e-41ef-83cc-900fac406f7f',
                'p4'
            )
            """
        )
        conn_b.execute("DELETE FROM child WHERE name='p1_child1'")

    # Set the argument order, i.e. which gpkg should be the rebased result
    if user_a_data_first:
        theirs, mine = user_a, user_b
    else:
        theirs, mine = user_b, user_a

    # Act (this will raise a GeoDiffLibError if geodiff cannot handle foreign keys)
    geodiff.rebase(str(original), str(theirs), str(mine), str(conflict))

    # Assert that rebased database contains expected changes
    assert_names("parent", expected_parents, mine)
    assert_names("child", expected_children, mine)


def assert_names(table: str, expected_names: set[str], db: Path) -> bool:
    with sqlite3.connect(db) as conn:
        names = set(
            row[0] for row in
            conn.execute(f"SELECT name FROM {table}").fetchall()
        )
    assert names == expected_names


def create_gpkg_files(tmp_path: Path, fk_constrained: bool = True) -> Tuple[Path, Path, Path]:
    """
    Create 3 GeoPackage files at the given filepaths, create the
    same tables with the same original data: two parents, each with
    two children.
    """
    original = tmp_path / "original.gpkg"
    user_a = tmp_path / "user_a.gpkg"
    user_b = tmp_path / "user_b.gpkg"

    # Define tables
    create_parent_sql = """
        CREATE TABLE parent (
            fid INTEGER PRIMARY KEY,
            uuid TEXT UNIQUE NOT NULL,
            name TEXT
        )"""
    create_child_sql = """
        CREATE TABLE child (
            fid INTEGER PRIMARY KEY,
            uuid TEXT UNIQUE NOT NULL,
            parent_uuid TEXT NOT NULL,
            name TEXT,
            FOREIGN KEY("parent_uuid") REFERENCES "parent"("uuid")
        )"""

    if not fk_constrained:
        # Remove database constraints from table definitions
        create_parent_sql = re.sub(r" UNIQUE", "", create_parent_sql)
        create_child_sql = re.sub(r" UNIQUE", "", create_child_sql)
        create_child_sql = re.sub(r",.*FOREIGN.*REFERENCES.*\)", "", create_child_sql)

    # Define test data
    parent_data = [
        {"uuid": "8b0cbf9c-cb22-4b7a-a4cf-4a4c31f48732", "name": "p1"},
        {"uuid": "3dcae64d-afe0-4233-b771-437b7ca9ed99", "name": "p2"},
    ]
    child_data = [
        {
            "uuid": "3544b178-2521-4b7c-a33e-1b10e8c6264e",
            "parent_uuid": "8b0cbf9c-cb22-4b7a-a4cf-4a4c31f48732",
            "name": "p1_child1",
        },
        {
            "uuid": "b64f3590-be92-4dad-b30f-a450c94846b8",
            "parent_uuid": "8b0cbf9c-cb22-4b7a-a4cf-4a4c31f48732",
            "name": "p1_child2",
        },
        {
            "uuid": "761b633a-d31d-4ea4-96e8-247aef434bc8",
            "parent_uuid": "3dcae64d-afe0-4233-b771-437b7ca9ed99",
            "name": "p2_child1",
        },
        {
            "uuid": "54a6feba-4297-400b-bf8c-058cd16b8572",
            "parent_uuid": "3dcae64d-afe0-4233-b771-437b7ca9ed99",
            "name": "p2_child2",
        },
    ]

    # Create geopackages
    for gpkg in [original, user_a, user_b]:
        with sqlite3.connect(gpkg) as conn:
            conn.execute(create_parent_sql)
            conn.execute(create_child_sql)

            conn.executemany(
                """
                INSERT INTO parent (uuid, name)
                VALUES (:uuid, :name)
                """,
                (row for row in parent_data)
            )
            conn.executemany(
                """
                INSERT INTO child (uuid, parent_uuid, name)
                VALUES (:uuid, :parent_uuid, :name)
                """,
                (row for row in child_data)
            )

    return original, user_a, user_b

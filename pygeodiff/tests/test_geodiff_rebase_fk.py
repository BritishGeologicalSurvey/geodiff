"""
This file tests the behaviour of geodiff when there are database-level constraints
applied to the tables.

Some of the tests are expected to fail until issue 210 has been resolved.
https://github.com/MerginMaps/geodiff/issues/210

The behaviour of GeoDiff in scenarios where combining changes from two users will
cause a database constraint to fail is as-yet undefined.  Perhaps it should raise
a GeoDiffConstraintError that reports the error message from the database with
information about the constraint that was broken.
"""

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
    "db_constrained",
    [
        pytest.param(False, id="no_db_constraint"),
        pytest.param(
            True,
            marks=pytest.mark.xfail(
                raises=GeoDiffLibError,
                reason="Expected to fail due to issue 210, when this xpasses remove this decorator",
            ),
            id="dbconstraint",
        ),
    ],
)
@pytest.mark.parametrize(
    "user_a_data_first", [True, False], ids=["user_a_data_first", "user_b_data_first"]
)
def test_geodiff_rebase_fk_happy_path(db_constrained, user_a_data_first, tmp_path):
    """
    This test checks that rebase succeeds on simple changes on tables with
    foreign key constraints.  This test applies INSERT, UPDATE and DELETE
    changes that should not produce any conflicts.
    """
    # Arrange
    geodiff = pygeodiff.GeoDiff(GEODIFFLIB)
    conflict = tmp_path / "conflict.txt"
    original, user_a, user_b = create_db_files(tmp_path, db_constrained=db_constrained)

    # Apply changes to databases to give the following expected values
    expected = {
        "species": [
            {"species_id": "MPL", "name": "Maple"},
            {"species_id": "OAK", "name": "Oak"},
            # 'PIN' deleted
            {"species_id": "SPC", "name": "Spruce"},  # inserted
            {"species_id": "BCH", "name": "Birch"},  # inserted
        ],
        "trees": [
            {"tree_id": 20251103001, "species_id": "MPL", "age": 1},  # age updated
            {"tree_id": 20251103002, "species_id": "OAK", "age": 99},  # age updated
            # 20241103003 deleted
            {"tree_id": 20251103004, "species_id": "SPC", "age": 29},  # inserted
            {"tree_id": 20251103005, "species_id": "BCH", "age": 46},  # inserted
        ]
    }

    with sqlite3.connect(user_a) as conn_a:
        conn_a.execute(
            """
            INSERT INTO species (species_id, name) VALUES (
                'BCH', 'Birch'
            )
            """
        )
        conn_a.execute(
            """
            INSERT INTO trees (tree_id, species_id, age) VALUES (
                20251103005, 'BCH', 46
            )
            """
        )
        conn_a.execute(
            """
            DELETE FROM trees WHERE tree_id IS 20251103003
            """
        )
        conn_a.execute(
            """
            DELETE FROM species WHERE species_id IS 'PIN'
            """
        )
        conn_a.execute(
            """
            UPDATE trees SET age=1 WHERE tree_id=20251103001
            """
        )

    with sqlite3.connect(user_b) as conn_b:
        conn_b.execute(
            """
            INSERT INTO species (species_id, name) VALUES (
                'SPC', 'Spruce'
            )
            """
        )
        conn_b.execute(
            """
            INSERT INTO trees (tree_id, species_id, age) VALUES (
                20251103004, 'SPC', 29
            )
            """
        )
        conn_b.execute(
            """
            UPDATE trees SET age=99 WHERE tree_id=20251103002
            """
        )

    # Set the argument order, i.e. which db should be the rebased result
    if user_a_data_first:
        theirs, mine = user_a, user_b
    else:
        theirs, mine = user_b, user_a

    # Act (this will raise a GeoDiffLibError if geodiff cannot handle foreign keys)
    geodiff.rebase(str(original), str(theirs), str(mine), str(conflict))

    # Assert that rebased database contains expected changes
    assert_data_as_expected(mine, expected)


def assert_data_as_expected(db: Path, expected_data: dict[str, list[dict]]):
    """
    Assert that table contents are as expected.  Use `set` comparison because
    the row ordering depends on the order that tables are passed to `rebase`.
    """
    with sqlite3.connect(db) as conn:
        for table, rows in expected_data.items():
            column_names = rows[0].keys()
            results = conn.execute(
                f"""SELECT {", ".join(column_names)} FROM {table}"""
                ).fetchall()

            assert set(results) == set(tuple(row.values()) for row in rows)


def create_db_files(tmp_path: Path, db_constrained: bool = True) -> Tuple[Path, Path, Path]:
    """
    Create 3 SQLite files at the given filepaths, each with the same tables
    and data.

    If `db_constrained`, the database will apply UNIQUE and FOREIGN
    KEY constraints.
    """
    original = tmp_path / "original.db"
    user_a = tmp_path / "user_a.db"
    user_b = tmp_path / "user_b.db"

    # Define tables
    create_species_sql = """
        CREATE TABLE species (
            fid INTEGER PRIMARY KEY,
            species_id TEXT UNIQUE,
            name TEXT
        )"""
    create_trees_sql = """
        CREATE TABLE trees (
            fid INTEGER PRIMARY KEY,
            tree_id INTEGER UNIQUE NOT NULL,
            species_id TEXT NOT NULL,
            age INTEGER,
            FOREIGN KEY("species_id") REFERENCES "species"("species_id")
        )"""

    if not db_constrained:
        # Remove database constraints from table definitions
        create_species_sql = re.sub(r" UNIQUE", "", create_species_sql)
        create_trees_sql = re.sub(r" UNIQUE", "", create_trees_sql)
        create_trees_sql = re.sub(r",.*FOREIGN.*REFERENCES.*\)", "", create_trees_sql)

    # Define initial data
    species_data = [
            {"species_id": "MPL", "name": "Maple"},
            {"species_id": "OAK", "name": "Oak"},
            {"species_id": "PIN", "name": "Pine"},
        ]
    trees_data = [
            {"tree_id": 20251103001, "species_id": "MPL", "age": 25},
            {"tree_id": 20251103002, "species_id": "OAK", "age": 30},
            {"tree_id": 20251103003, "species_id": "PIN", "age": 18},
        ]

    # Create geopackages
    with sqlite3.connect(original) as conn:
        conn.execute(create_species_sql)
        conn.execute(create_trees_sql)
        conn.executemany(
            """
            INSERT INTO species (species_id, name)
            VALUES (:species_id, :name)
            """,
            species_data
        )
        conn.executemany(
            """
            INSERT INTO trees (tree_id, species_id, age)
            VALUES (:tree_id, :species_id, :age)
            """,
            trees_data
        )

    user_a.write_bytes(original.read_bytes())
    user_b.write_bytes(original.read_bytes())

    return original, user_a, user_b

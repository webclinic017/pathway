import json
import threading
import time
import uuid

import pandas as pd
from pyiceberg.catalog import load_catalog

import pathway as pw
from pathway.internals.parse_graph import G
from pathway.tests.utils import wait_result_with_checker

INPUT_CONTENTS_1 = """{"id": 1, "name": "John"}
{"id": 2, "name": "Jane"}
{"id": 3, "name": "Alice"}
{"id": 4, "name": "Bob"}"""
INPUT_CONTENTS_2 = """{"id": 5, "name": "Peter"}
{"id": 6, "name": "Jake"}
{"id": 7, "name": "Dora"}
{"id": 8, "name": "Barbara"}"""
INPUT_CONTENTS_3 = """{"id": 9, "name": "Anna"}
{"id": 10, "name": "Paul"}
{"id": 11, "name": "Steve"}
{"id": 12, "name": "Sarah"}"""

CATALOG_URI = "http://iceberg:8181"
INPUT_CONTENTS = {
    1: INPUT_CONTENTS_1,
    2: INPUT_CONTENTS_2,
    3: INPUT_CONTENTS_3,
}


def _get_pandas_table(table_name: str) -> pd.DataFrame:
    catalog = load_catalog(name="default", uri=CATALOG_URI)
    table = catalog.load_table(table_name)
    scan = table.scan()
    return scan.to_pandas()


class IcebergEntriesCountChecker:
    def __init__(self, table_name: str, expected_count: int):
        self.table_name = table_name
        self.expected_count = expected_count

    def __call__(self):
        try:
            table = _get_pandas_table(self.table_name)
            return len(table) == self.expected_count
        except Exception:
            return False


def test_iceberg_several_runs(tmp_path):
    input_path = tmp_path / "input.txt"
    table_name = str(uuid.uuid4())
    all_ids = set()
    all_names = set()

    def run(seq_number: int):
        input_path.write_text(INPUT_CONTENTS[seq_number])
        for input_line in INPUT_CONTENTS[seq_number].splitlines():
            data = json.loads(input_line)
            all_ids.add(data["id"])
            all_names.add(data["name"])

        class InputSchema(pw.Schema):
            id: int
            name: str

        G.clear()
        table = pw.io.jsonlines.read(
            input_path,
            schema=InputSchema,
            mode="static",
        )
        pw.io.iceberg.write(
            table,
            catalog_uri=CATALOG_URI,
            namespace=["my_database"],
            table_name=table_name,
        )
        pw.run(monitoring_level=pw.MonitoringLevel.NONE)

        iceberg_table_name = f"my_database.{table_name}"
        pandas_table = _get_pandas_table(iceberg_table_name)
        assert pandas_table.shape == (4 * seq_number, 4)
        assert set(pandas_table["id"]) == all_ids
        assert set(pandas_table["name"]) == all_names
        assert set(pandas_table["diff"]) == {1}
        assert len(set(pandas_table["time"])) == seq_number

    # The first run includes the table creation.
    # The second and the following runs check that the case with the created table also works correctly.
    for seq_number in range(1, len(INPUT_CONTENTS) + 1):
        run(seq_number)


def test_iceberg_streaming(tmp_path):
    inputs_path = tmp_path / "inputs"
    inputs_path.mkdir()
    table_name = str(uuid.uuid4())

    def stream_inputs():
        for i in range(1, len(INPUT_CONTENTS) + 1):
            input_path = inputs_path / f"{i}.txt"
            input_path.write_text(INPUT_CONTENTS[i])
            time.sleep(5)

    class InputSchema(pw.Schema):
        id: int
        name: str

    table = pw.io.jsonlines.read(
        inputs_path,
        schema=InputSchema,
        mode="streaming",
    )
    pw.io.iceberg.write(
        table,
        catalog_uri=CATALOG_URI,
        namespace=["my_database"],
        table_name=table_name,
        min_commit_frequency=1000,
    )

    t = threading.Thread(target=stream_inputs)
    t.start()
    wait_result_with_checker(
        IcebergEntriesCountChecker(
            f"my_database.{table_name}", 4 * len(INPUT_CONTENTS)
        ),
        30,
    )

    all_ids = set()
    all_names = set()
    for i in range(1, len(INPUT_CONTENTS) + 1):
        for input_line in INPUT_CONTENTS[i].splitlines():
            data = json.loads(input_line)
            all_ids.add(data["id"])
            all_names.add(data["name"])

    iceberg_table_name = f"my_database.{table_name}"
    pandas_table = _get_pandas_table(iceberg_table_name)
    assert pandas_table.shape == (4 * len(INPUT_CONTENTS), 4)
    assert set(pandas_table["id"]) == all_ids
    assert set(pandas_table["name"]) == all_names
    assert set(pandas_table["diff"]) == {1}
    assert len(set(pandas_table["time"])) == len(INPUT_CONTENTS)

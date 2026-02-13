"""Excel data import and record navigation."""

import os
from typing import Optional

import openpyxl


class DataStore:
    """Loads an Excel workbook and provides record-level navigation."""

    def __init__(self):
        self.file_path: Optional[str] = None
        self.headers: list[str] = []
        self.rows: list[dict[str, str]] = []
        self._index: int = 0

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(self, path: str) -> None:
        """Read the first sheet of *path* and populate headers + rows."""
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            raise ValueError("Workbook has no active sheet.")

        raw_rows = list(ws.iter_rows(values_only=True))
        wb.close()

        if not raw_rows:
            raise ValueError("Spreadsheet is empty.")

        self.headers = [str(h).strip() if h is not None else f"Col{i+1}"
                        for i, h in enumerate(raw_rows[0])]
        self.rows = []
        for row in raw_rows[1:]:
            record: dict[str, str] = {}
            for i, hdr in enumerate(self.headers):
                val = row[i] if i < len(row) else None
                record[hdr] = "" if val is None else str(val)
            self.rows.append(record)

        self.file_path = path
        self._index = 0

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    @property
    def current_index(self) -> int:
        return self._index

    @current_index.setter
    def current_index(self, value: int) -> None:
        if self.rows:
            self._index = max(0, min(value, len(self.rows) - 1))

    @property
    def current_record(self) -> dict[str, str]:
        if not self.rows:
            return {}
        return self.rows[self._index]

    @property
    def count(self) -> int:
        return len(self.rows)

    def next(self) -> dict[str, str]:
        self.current_index += 1
        return self.current_record

    def prev(self) -> dict[str, str]:
        self.current_index -= 1
        return self.current_record

    def goto(self, index: int) -> dict[str, str]:
        self.current_index = index
        return self.current_record

    def search(self, query: str) -> list[int]:
        """Return row indices where any cell contains *query* (case-insensitive)."""
        q = query.lower()
        return [i for i, row in enumerate(self.rows)
                if any(q in v.lower() for v in row.values())]

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------
    def has_column(self, name: str) -> bool:
        return name in self.headers

    def missing_columns(self, names: list[str]) -> list[str]:
        return [n for n in names if n not in self.headers]

    def get_value(self, column: str, record: Optional[dict[str, str]] = None,
                  fallback: str = "") -> str:
        """Get a column value from *record* (default: current), with *fallback*."""
        rec = record if record is not None else self.current_record
        return rec.get(column, fallback)

"""Read-only, content-free migration evidence; no credentials or row values printed."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
with connection:
    result = {"integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
              "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall())}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("users", "subscriptions", "steam_targets", "snapshots", "activation_codes"):
        if table not in tables:
            continue
        rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
        payload = json.dumps(rows, default=lambda value: value.hex() if isinstance(value, bytes) else str(value), ensure_ascii=False).encode()
        result[table] = {"count": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}
print(json.dumps(result, sort_keys=True))
connection.close()

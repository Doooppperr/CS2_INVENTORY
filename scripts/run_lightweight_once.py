"""对 76561199260055147 跑一次轻量轮（single_inventory 路径），仅用于本地验证。

不读取历史快照（不经过 store_snapshot/数据库），观测缓存使用临时目录，
保证结果只反映本次单库存端点 + 官方公开接口的实时返回。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cs2_inventory.inventory_engine import load_steamwebapi_key, run_lightweight_query
from cs2_inventory.unified import public_payload, unify_inventory


def main() -> int:
    steamid = "76561199260055147"
    key = load_steamwebapi_key(None, None)
    if not key:
        print("缺少 Steamwebapi key（未找到 steamwebapi_key.txt 且未设置 STEAMWEBAPI_KEY）", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as directory:
        cache = str(Path(directory) / "obs.json")
        result = run_lightweight_query(
            steamid,
            key=key,
            language="schinese",
            timeout=120,
            observation_cache_path=cache,
            single_inventory=True,
        )
        unified = unify_inventory(result)
        payload = public_payload(unified, scanned_at=None)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("---counts---")
        print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
        print("---coverage---")
        print(json.dumps(result["coverage"], ensure_ascii=False, indent=2))
        print("---sources---")
        print(json.dumps(result["sources"], ensure_ascii=False, indent=2))
        print("---errors---")
        print(json.dumps(result["errors"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

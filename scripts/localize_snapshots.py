#!/usr/bin/env python3
from __future__ import annotations

import collections
import gzip
import json
import re

from cs2_inventory.app import create_app
from cs2_inventory.database import db
from cs2_inventory.inventory_engine import (
    AssetRecord,
    _read_observation_cache,
    localize_asset_records,
)
from cs2_inventory.models import Snapshot, SteamTarget
from cs2_inventory.services import snapshot_public


def main() -> int:
    app = create_app()
    cache_path = app.config["OBSERVATION_CACHE"]
    language = app.config["ITEM_LANGUAGE"]
    with app.app_context():
        observations = _read_observation_cache(cache_path)
        changed = 0
        for target in SteamTarget.query.all():
            snapshot = (
                Snapshot.query.filter_by(target_id=target.id)
                .order_by(Snapshot.scanned_at.desc())
                .first()
            )
            if snapshot is None:
                continue
            account = observations.get(target.steamid)
            account = account if isinstance(account, dict) else {}
            rows = [
                row
                for row in snapshot.items
                if not re.search(r"[\u4e00-\u9fff]", row.name)
            ]
            records = []
            linked_rows = []
            for row in rows:
                observed = account.get(row.asset_key)
                if not isinstance(observed, dict):
                    continue
                records.append(
                    AssetRecord(
                        assetid=row.asset_key,
                        classid=str(observed.get("classid") or ""),
                        instanceid=str(observed.get("instanceid") or "0"),
                        name=row.name,
                        amount=row.amount,
                        appid="730",
                        contextid="2",
                    )
                )
                linked_rows.append(row)
            localized = localize_asset_records(
                records, language=language, cache_path=cache_path
            )
            for row, record in zip(linked_rows, localized):
                if row.name != record.name:
                    row.name = record.name
                    changed += 1
            counts = collections.Counter(item.name for item in snapshot.items)
            snapshot.item_types = len(counts)
            public = snapshot_public(snapshot)
            snapshot.payload_gzip = gzip.compress(
                json.dumps(public, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        db.session.commit()
    print(f"LOCALIZED_SNAPSHOT_ITEMS={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

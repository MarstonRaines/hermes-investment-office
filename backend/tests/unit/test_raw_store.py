# =====================================================================
# tests/unit/test_raw_store.py —— Raw Evidence Store（TS-05 §7，冻结）
# =====================================================================
from __future__ import annotations

import asyncio
import hashlib

from app.providers.raw_store import RawEvidenceStore


def test_save_returns_hash_and_key(tmp_path) -> None:
    store = RawEvidenceStore(tmp_path)
    content = b'{"trade_date": "2026-08-21", "close": 138.5}'

    async def run() -> None:
        art = await store.save("tushare", "market_sync_job", "daily_600519.SH_2026-08-21.json", content)
        assert art.raw_hash == hashlib.sha256(content).hexdigest()
        assert art.raw_object_key.startswith("raw/tushare/")
        assert art.raw_object_key.endswith("market_sync_job/daily_600519.SH_2026-08-21.json")
        assert art.size_bytes == len(content)
        # 字节级原样：不转换不截断
        assert store.read(art.raw_object_key) == content

    asyncio.run(run())


def test_verify_roundtrip(tmp_path) -> None:
    store = RawEvidenceStore(tmp_path)

    async def run() -> None:
        art = await store.save("akshare_sina", "market_sync_job", "daily_sh600519.json", b"abc")
        assert store.verify(art.raw_hash, art.raw_object_key) is True
        # 篡改 → 指纹不匹配
        target = store.base_dir / art.raw_object_key
        target.write_bytes(b"tampered")
        assert store.verify(art.raw_hash, art.raw_object_key) is False

    asyncio.run(run())


def test_missing_artifact_raises(tmp_path) -> None:
    store = RawEvidenceStore(tmp_path)
    try:
        store.read("raw/tushare/2026-08-21/market_sync_job/x.json")
        raise AssertionError("should raise")
    except FileNotFoundError:
        pass

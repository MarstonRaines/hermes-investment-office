# =====================================================================
# backend/app/providers/raw_store.py —— Raw Evidence Store（TS-05 §7，冻结）
#
# 原则（冻结规范 §8.3/§14.1）：尽量原样保存、可重复解析、可追溯。
# - 路径：data/raw/{provider}/{yyyy-mm-dd}/{job_name}/{payload}
# - raw_hash = sha256(原始字节)：内容指纹（重解析校验/去重/防篡改）
# - raw_object_key = 相对 data/ 的路径（provenance_records.raw_object_key）
# - 一个 artifact → N 条事实：每条事实的 provenance 引用同一 raw_hash/key
# - 永久保留（纳入备份），仅缓存层可 TTL 删除
# =====================================================================
from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from pathlib import Path

__all__ = ["RawArtifact", "RawEvidenceStore"]


class RawArtifact:
    """一次接口调用完整响应的落盘结果。"""

    def __init__(self, raw_hash: str, raw_object_key: str, size_bytes: int) -> None:
        self.raw_hash = raw_hash
        self.raw_object_key = raw_object_key
        self.size_bytes = size_bytes


class RawEvidenceStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def _target(self, provider: str, job_name: str, label: str, day: date | None = None) -> Path:
        day = day or date.today()
        return (
            self.base_dir
            / "raw"
            / provider
            / day.strftime("%Y-%m-%d")
            / job_name
            / label
        )

    async def save(
        self,
        provider: str,
        job_name: str,
        label: str,
        content: bytes,
        *,
        day: date | None = None,
    ) -> RawArtifact:
        """原样字节落盘（不转换、不截断、不重排），返回 raw_hash + raw_object_key。

        label 只作人类可读检索标签（含 provider symbol 与日期），不参与业务主键。
        """
        target = self._target(provider, job_name, label, day)
        await asyncio.to_thread(self._write, target, content)
        raw_hash = hashlib.sha256(content).hexdigest()
        rel = target.relative_to(self.base_dir)
        return RawArtifact(
            raw_hash=raw_hash,
            raw_object_key=str(rel),
            size_bytes=len(content),
        )

    def _write(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def exists(self, provider: str, job_name: str, label: str, *, day: date | None = None) -> bool:
        return self._target(provider, job_name, label, day).exists()

    def read(self, raw_object_key: str) -> bytes:
        """按 raw_object_key（相对 data/）读取 artifact（重解析入口）。"""
        path = self.base_dir / raw_object_key
        if not path.exists():
            raise FileNotFoundError(f"raw artifact 不存在: {path}")
        return path.read_bytes()

    def verify(self, raw_hash: str, raw_object_key: str) -> bool:
        """重解析校验：内容指纹一致（防篡改/去重）。"""
        try:
            content = self.read(raw_object_key)
        except FileNotFoundError:
            return False
        return hashlib.sha256(content).hexdigest() == raw_hash

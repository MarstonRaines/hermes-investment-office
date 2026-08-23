"""架构测试（冻结规范 §46 / TS-08 ARCH-* 组，M0 子集）。

机器化纪律：
1. 模块依赖方向（api 薄层纯度、providers 隔离）
2. 数据库访问边界（TABLE_OWNER 白名单：40 表全注册、无孤儿表、归属零违规）
3. 物理隔离（docker-compose.yml 生产形态不暴露 db 端口）
4. append-only 触发器存在性（14 表 × UPDATE/DELETE）
5. CHECK 约束（base_currency=CNY、quality_score 0-1）
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

import app.models  # noqa: F401 —— 注册全部 ORM 模型到 Base.metadata
from app.common.base import Base
from app.registry import TABLE_OWNER

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = BACKEND_ROOT / "app"
COMPOSE_FILE = BACKEND_ROOT.parent / "docker-compose.yml"

# ---------------------------------------------------------------
# 1. 模块依赖方向（ARCH-DEP）
# ---------------------------------------------------------------

# api/ 允许 import 的 app 内部模块（薄适配层纯度，TS-03 §6.1）
API_ALLOWED_PREFIXES = ("app.common", "app.api")
# api/ 禁止 import 的模块（业务实现细节）
API_FORBIDDEN = ("providers",)


def _module_imports(path: Path) -> list[str]:
    """提取模块的 import 语句（含 from ... import 的模块名）。"""
    src = path.read_text()
    imports = []
    for m in re.finditer(r"^(?:from|import)\s+([\w.]+)", src, re.M):
        imports.append(m.group(1))
    return imports


def test_api_layer_purity():
    """ARCH-API：api/ 模块不得 import providers 等业务实现。"""
    for py in (APP_ROOT / "api").rglob("*.py"):
        if py.name == "__init__.py":
            continue
        for mod in _module_imports(py):
            if mod.startswith("app."):
                assert not any(mod.startswith(f"app.{f}") for f in API_FORBIDDEN), (
                    f"{py.relative_to(APP_ROOT)} 违反 api 纯度：import {mod}"
                )
                assert mod.startswith(tuple(API_ALLOWED_PREFIXES)) or ".schemas" in mod or ".service" in mod, (
                    f"{py.relative_to(APP_ROOT)} 越界 import {mod}"
                )


def test_providers_not_imported_by_engines():
    """ARCH-DEP：引擎域（valuation/portfolio/risk/etf）不得 import providers。"""
    engine_domains = ("valuation", "portfolio", "risk", "etf")
    for domain in engine_domains:
        domain_dir = APP_ROOT / domain
        if not domain_dir.exists():
            continue
        for py in domain_dir.rglob("*.py"):
            for mod in _module_imports(py):
                assert not mod.startswith("app.providers"), (
                    f"{domain}/ 反向依赖 providers（{py.name} → {mod}）"
                )


# ---------------------------------------------------------------
# 2. 数据库访问边界（ARCH-DB）
# ---------------------------------------------------------------

def test_all_tables_registered():
    """ARCH-DB-001：TABLE_OWNER 与 Base.metadata 完全一致（40 表、无孤儿）。"""
    tables = set(Base.metadata.tables)
    assert set(TABLE_OWNER) == tables
    assert len(tables) == 40


def test_table_owner_module_match():
    """ARCH-DB-002：每张表定义模块 == TABLE_OWNER 归属。"""
    cls_map = {c.__tablename__: c for c in Base.__subclasses__()}
    for table, owner in TABLE_OWNER.items():
        cls = cls_map.get(table)
        assert cls is not None, f"{table} 无模型类"
        assert cls.__module__.startswith(f"app.{owner}"), (
            f"{table} 归属 {owner}，但定义于 {cls.__module__}"
        )


def test_primary_keys_are_uuid():
    """ARCH-DB-003：每张表主键为 UUID（业务键禁止作主键）。"""
    from sqlalchemy.types import Uuid

    for name, table in Base.metadata.tables.items():
        pk_cols = list(table.primary_key.columns)
        assert len(pk_cols) == 1, f"{name} 主键列数 != 1"
        assert isinstance(pk_cols[0].type, Uuid), f"{name} 主键非 UUID"


# ---------------------------------------------------------------
# 3. 物理隔离（ARCH-INF，冻结规范 §4.4 / ADR-004）
# ---------------------------------------------------------------

def test_compose_db_no_port_exposure():
    """ARCH-INF-001：生产 docker-compose.yml 的 db 服务不映射端口。"""
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    db_svc = compose["services"]["db"]
    assert "ports" not in db_svc, "db 服务不得映射端口到宿主机（§4.4 物理隔离）"


def test_compose_backend_loopback_only():
    """ARCH-INF-002：backend 端口仅绑定 127.0.0.1。"""
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    ports = compose["services"]["backend"].get("ports", [])
    for p in ports:
        assert p.startswith("127.0.0.1:"), f"backend 端口必须绑定回环：{p}"


def test_settings_default_localhost():
    """ARCH-INF-003：settings 默认绑定 127.0.0.1、认证关闭（ADR-004 D5）。"""
    from app.common.config import Settings

    s = Settings(_env_file=None)
    assert s.bind_host == "127.0.0.1"
    assert s.auth_enabled is False


# ---------------------------------------------------------------
# 4. append-only 触发器（ARCH-DB-004，ts02 §10.3）
# ---------------------------------------------------------------

APPEND_ONLY_TABLES = [
    "provenance_records", "thesis_revisions", "portfolio_transactions",
    "valuation_runs", "valuation_assumptions", "valuation_input_refs",
    "evidence_items", "evidence_links", "audit_events", "outbox_events",
    "position_snapshots", "portfolio_snapshots", "thesis_events", "research_events",
]


def test_append_only_triggers_exist(raw_engine):
    """14 张不可变表必须存在拒绝 UPDATE/DELETE 的触发器。"""
    with raw_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT event_object_table, event_manipulation "
            "FROM information_schema.triggers "
            "WHERE trigger_name = 'trg_' || event_object_table || '_no_update'"
        )).fetchall()
    by_table = {}
    for table, event in rows:
        by_table.setdefault(table, set()).add(event)
    for t in APPEND_ONLY_TABLES:
        assert t in by_table, f"{t} 缺少 append-only 触发器"
        assert {"UPDATE", "DELETE"} <= by_table[t], f"{t} 触发器事件不完整"


def test_append_only_trigger_enforces(raw_engine):
    """物理兜底生效：UPDATE/DELETE 必须被拒绝（INSERT 不受影响）。"""
    with raw_engine.connect() as conn:
        # INSERT 正常
        conn.execute(text(
            "INSERT INTO provenance_records (provenance_id, source_kind, source, provider, "
            "observed_at, retrieved_at, quality_score, quality_status, transform_version) "
            "VALUES (gen_random_uuid(), 'PROVIDER', 'arch-test', 'internal', now(), now(), "
            "0.5, 'ACCEPTABLE', 'arch/0.1')"
        ))
        conn.commit()
        # UPDATE 被拒
        with pytest.raises(Exception):
            conn.execute(text("UPDATE provenance_records SET provider='x'"))
            conn.rollback()
        # DELETE 被拒
        with pytest.raises(Exception):
            conn.execute(text("DELETE FROM provenance_records WHERE provider='internal'"))
            conn.rollback()


# ---------------------------------------------------------------
# 5. CHECK 约束（ARCH-DB-005）
# ---------------------------------------------------------------

def test_check_constraints_present(raw_engine):
    """关键 CHECK 存在：base_currency=CNY、quality_score 0-1、QDII 关联。"""
    with raw_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE contype='c'"
        )).fetchall()
    defs = {(t, n): d for t, n, d in rows}
    assert any("base_currency" in d and "CNY" in d for (t, n), d in defs.items()), "缺少 base_currency=CNY CHECK"
    assert any("quality_score" in d and "0" in d and "1" in d for (t, n), d in defs.items()), "缺少 quality_score 0-1 CHECK"
    assert any("is_qdii" in d for (t, n), d in defs.items()), "缺少 QDII 关联 CHECK"


def test_alembic_check_clean(raw_engine):
    """ARCH-DB-006：ORM metadata 与已迁移 DB schema 零差异（alembic check 等价物）。

    通过比对所有约束名/索引名（PG 63 字符截断处理）验证迁移往返一致性。
    防止命名约定（convention）二次套用导致的双前缀/截断漂移。
    """
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(raw_engine)
    db_tables = set(insp.get_table_names()) - {"alembic_version"}  # 迁移管理表排除
    orm_tables = set(Base.metadata.tables)
    assert db_tables == orm_tables, f"表集合不一致: {db_tables ^ orm_tables}"

    for table in orm_tables:
        db_ck = {c["name"] for c in insp.get_check_constraints(table)}
        orm_ck = {
            c.name for c in Base.metadata.tables[table].constraints
            if c.__class__.__name__ == "CheckConstraint" and c.name
        }
        # 截断处理：PG 截断到 63 字符
        db_ck_63 = {n[:63] for n in db_ck}
        orm_ck_63 = {n[:63] for n in orm_ck}
        assert db_ck_63 == orm_ck_63, f"{table} CHECK 不一致: db={db_ck_63 ^ orm_ck_63}"

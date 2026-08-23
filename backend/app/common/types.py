# backend/app/common/types.py —— 精度常量与时间类型（ts02 §1.2 全库统一）
from sqlalchemy import TIMESTAMP, Numeric

# TIMESTAMPTZ：SQLAlchemy 2.0 未从 dialects.postgresql 直接导出该名，
# 全库统一在此定义别名（= TIMESTAMP(timezone=True)），模型一律从本模块导入。
TIMESTAMPTZ = TIMESTAMP(timezone=True)

MONEY = Numeric(18, 4)    # 金额（CNY）
PRICE = Numeric(12, 6)    # NAV / 单价 / 指数点位
QTY = Numeric(24, 6)      # 数量 / 份额
RATIO = Numeric(10, 6)    # 比率（0-1）
QUALITY = Numeric(5, 4)   # quality_score
FX_RATE = Numeric(16, 8)  # 汇率 / 假设值 / 复权因子
PE_PB = Numeric(12, 4)    # index_pe / index_pb
FACT = Numeric(24, 6)     # financial_facts value / original_value
LOT = Numeric(12, 0)      # lot_size



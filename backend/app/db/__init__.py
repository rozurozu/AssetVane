"""DB 層（SQLAlchemy Core）。

DB に触れるのは FastAPI だけ（ADR-005）。スキーマは Python 側に一元化（data-model.md）。
ORM は使わず Core の Expression Language で組み立てる（バルク UPSERT 主役・pandas 連携・
二重モデリング回避）。MetaData/Table を単一の真実にし、将来 Alembic に乗れる形にしておく。
"""

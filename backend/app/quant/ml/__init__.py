"""AI Alpha Scorer の数理（特徴量・学習・推論）の純関数群（Phase 5・ADR-006/014/016）。

設計の真実: docs/phase-specs/phase5-spec.md。
- **DB を知らない純関数**（ADR-016）。引数は DataFrame / dict / list、戻り値も素のデータ。
- 計算の真実はここだけ。学習（train.py・別 PC）と推論（infer.py・ラズパイ）で
  **同一の特徴量定義**（features.py）を共有し、再現性を担保する。
"""

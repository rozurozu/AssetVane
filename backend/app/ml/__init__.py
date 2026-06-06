"""学習済みモデル（.pkl）の配置・読込・バージョニング（Phase 5・data-arch・ADR-006/018）。

設計の真実: docs/phase-specs/phase5-spec.md §4.2。
- **ファイル I/O のみ・DB は知らない**。`.pkl`＋メタ JSON の読込と検証を担う（推論は quant/ml）。
- 学習は別 PC（ADR-006）。ここはラズパイ側で現用モデルを引く口。
"""

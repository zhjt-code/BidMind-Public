"""L3 完整候选 shadow 评估脚本入口——只读、支持 --dry-run / --shadow。

职责: 提供 CLI 入口与公共符号重导出；所有实现在子包 scoring_full_candidate_eval/ 中。
不做: 不写入业务数据库，不修改评分项，不改变任何配置，不记录评分项原文。

用法:
    cd backend
    # 只验证语料/注入，不调用模型
    .venv/Scripts/python -m scripts.evaluate_scoring_full_candidate --dry-run

    # 尝试真实 shadow（需 LLM 环境就绪）
    .venv/Scripts/python -m scripts.evaluate_scoring_full_candidate --shadow
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

# ---------------------------------------------------------------------------
# 公共符号重导出（保持与旧单文件接口兼容，测试 import 路径不变）
# ---------------------------------------------------------------------------

from scripts.scoring_full_candidate_eval import (  # noqa: E402
    InjectionKind,
    EvalSample,
    EvalMetrics,
    apply_injection,
    build_synthetic_corpus,
    compute_metrics,
    run_dry_run,
    run_shadow_preflight,
    run_shadow_evaluation,
    _anchor_rate,
    _rule_fidelity,
)

__all__ = [
    "InjectionKind",
    "EvalSample",
    "EvalMetrics",
    "apply_injection",
    "build_synthetic_corpus",
    "compute_metrics",
    "run_dry_run",
    "run_shadow_preflight",
    "run_shadow_evaluation",
    "_anchor_rate",
    "_rule_fidelity",
]


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI 入口：--dry-run 或 --shadow。"""
    import argparse
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")

    parser = argparse.ArgumentParser(description="L3 完整候选 shadow 评估脚本（只读）")
    parser.add_argument("--dry-run", action="store_true", help="只验证语料/注入/统计，不调用模型")
    parser.add_argument("--shadow", action="store_true", help="尝试一次真实 shadow 评估（需 LLM 环境就绪）")
    args = parser.parse_args()

    corpus = build_synthetic_corpus()

    # 始终先跑 dry-run 报告
    dry_report = run_dry_run(corpus)
    print(json.dumps(dry_report, ensure_ascii=False, indent=2))

    if not (args.dry_run or args.shadow):
        logger.info("未指定 --dry-run 或 --shadow，仅输出 dry-run 报告")
        return 0

    if args.shadow:
        preflight = run_shadow_preflight(corpus)
        print(json.dumps(preflight, ensure_ascii=False, indent=2))

        if not preflight.get("environment_ok"):
            logger.warning(
                f"[shadow] 环境阻断，不执行真实调用: {preflight.get('blocked_reason')}"
            )
            return 1

        try:
            from app.core.ai.ai_factory import get_review_client
            llm_client = get_review_client()
        except Exception as exc:  # noqa: BLE001
            blocked = f"get_review_client_failed: {type(exc).__name__}: {exc}"
            logger.warning(f"[shadow] {blocked}")
            print(json.dumps({"blocked_reason": blocked}, ensure_ascii=False))
            return 1

        shadow_report = asyncio.run(run_shadow_evaluation(corpus, llm_client))
        print(json.dumps(shadow_report, ensure_ascii=False, indent=2))
        if shadow_report.get("blocked_reason"):
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

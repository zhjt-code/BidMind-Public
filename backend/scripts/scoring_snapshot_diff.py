"""评分提取 T0 快照差分器（治理阶段 1 的零倒退判据入口）。

职责: 把当前代码的真实链路产物与已批准的 T0 基线逐样本、逐项、逐字段比对，
      任何差异以可读形式报出并以非零退出码失败。
不做: 不自动更新基线（圆桌不可让步项：基线禁自动重写，
      基线变更必须由人显式执行 scoring_snapshot.py --write 并单独提交）。

用法（仓库根执行）:
    .venv\\Scripts\\python.exe backend/scripts/scoring_snapshot_diff.py

退出码: 0 = 零差分；1 = 存在差分；2 = 基线缺失。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from scoring_snapshot import BASELINE_DIR, build_snapshot  # noqa: E402

BASELINE_PATH = BASELINE_DIR / "scoring_t0_snapshot.json"
MAX_REPORTED = 40


def _item_key(item: dict) -> str:
    return f"{item.get('score_item_name')}|{item.get('score_weight')}"


def _diff_items(sample_id: str, base_items: list, cur_items: list) -> list[str]:
    diffs: list[str] = []
    if len(base_items) != len(cur_items):
        diffs.append(
            f"[{sample_id}] 条目数变化: {len(base_items)} -> {len(cur_items)}"
        )
    for index, (base, cur) in enumerate(zip(base_items, cur_items)):
        for field in base.keys() | cur.keys():
            old, new = base.get(field), cur.get(field)
            if old != new:
                diffs.append(
                    f"[{sample_id}] item#{index} ({_item_key(base)}) "
                    f"字段 {field}: {old!r} -> {new!r}"
                )
    return diffs


def _diff_scalar(sample_id: str, name: str, base, cur) -> list[str]:
    if base == cur:
        return []
    return [f"[{sample_id}] {name}: {base!r} -> {cur!r}"]


def _diff_trace(sample_id: str, base: dict, cur: dict) -> list[str]:
    """裁决分支命中差分——回答"哪条裁决变了"，是定位的第二层。"""
    diffs: list[str] = []
    for branch_id in sorted(set(base) | set(cur)):
        old, new = base.get(branch_id, 0), cur.get(branch_id, 0)
        if old == new:
            continue
        if old == 0:
            diffs.append(f"[{sample_id}] 新命中裁决分支 {branch_id} ({new} 次)")
        elif new == 0:
            diffs.append(f"[{sample_id}] 不再命中裁决分支 {branch_id} (原 {old} 次)")
        else:
            diffs.append(f"[{sample_id}] 裁决分支 {branch_id} 命中 {old} -> {new} 次")
    return diffs


def _diff_ledger(sample_id: str, base: dict | None, cur: dict | None) -> list[str]:
    """账本报告差分——提取链之外的第二条结论通道（status/基准/缺口）。"""
    if base is None and cur is None:
        return []
    if base is None:
        return [f"[{sample_id}] 账本: 基线无报告 -> 本次产出报告"]
    if cur is None:
        return [f"[{sample_id}] 账本: 基线有报告 -> 本次无报告"]

    diffs: list[str] = []
    for field in sorted(set(base) | set(cur)):
        old, new = base.get(field), cur.get(field)
        if old == new:
            continue
        if field in ("group_rows", "scope_reports"):
            old_rows, new_rows = old or [], new or []
            if len(old_rows) != len(new_rows):
                diffs.append(
                    f"[{sample_id}] 账本 {field} 行数变化: "
                    f"{len(old_rows)} -> {len(new_rows)}"
                )
            for idx, (o, n) in enumerate(zip(old_rows, new_rows)):
                for key in sorted(set(o) | set(n)):
                    if o.get(key) != n.get(key):
                        diffs.append(
                            f"[{sample_id}] 账本 {field}#{idx} "
                            f"{key}: {o.get(key)!r} -> {n.get(key)!r}"
                        )
        else:
            diffs.append(f"[{sample_id}] 账本 {field}: {old!r} -> {new!r}")
    return diffs


def _diff_confirm(sample_id: str, base: dict | None, cur: dict | None) -> list[str]:
    """确认页账本口径差分——与提取 shadow 口径并列的第三条结论通道。

    该口径多经一次「落库元数据往返 + declaration_pages 补喂」，
    与 shadow 口径结论可以不同，故独立报出而非合并。
    """
    if base is None and cur is None:
        return []
    if base is None:
        return [f"[{sample_id}] 确认页账本: 基线无报告 -> 本次产出报告"]
    if cur is None:
        return [f"[{sample_id}] 确认页账本: 基线有报告 -> 本次无报告"]

    diffs: list[str] = []
    for field in sorted(set(base) | set(cur)):
        old, new = base.get(field), cur.get(field)
        if old == new:
            continue
        if field in ("group_rows", "scope_reports") and isinstance(old, list):
            new_rows = new if isinstance(new, list) else []
            if len(old) != len(new_rows):
                diffs.append(
                    f"[{sample_id}] 确认页账本 {field} 行数变化: "
                    f"{len(old)} -> {len(new_rows)}"
                )
            for idx, (o, n) in enumerate(zip(old, new_rows)):
                for key in sorted(set(o) | set(n)):
                    if o.get(key) != n.get(key):
                        diffs.append(
                            f"[{sample_id}] 确认页账本 {field}#{idx} "
                            f"{key}: {o.get(key)!r} -> {n.get(key)!r}"
                        )
        else:
            diffs.append(f"[{sample_id}] 确认页账本 {field}: {old!r} -> {new!r}")
    return diffs


def diff_snapshots(baseline: dict, current: dict) -> list[str]:
    diffs: list[str] = []
    base_samples: dict = baseline.get("samples", {})
    cur_samples: dict = current.get("samples", {})

    missing = sorted(set(base_samples) - set(cur_samples))
    added = sorted(set(cur_samples) - set(base_samples))
    for sample_id in missing:
        diffs.append(f"[{sample_id}] 基线存在但本次缺失（语料被删除或未被收集）")
    for sample_id in added:
        diffs.append(f"[{sample_id}] 本次新增语料（基线未批准，需显式 --write 纳入）")

    for sample_id in sorted(set(base_samples) & set(cur_samples)):
        base, cur = base_samples[sample_id], cur_samples[sample_id]
        for field in ("chunk_count", "item_count", "parse_diagnostics_count",
                      "llm_calls", "db_executes", "ledger_calls", "confirm_db_reads"):
            diffs.extend(_diff_scalar(sample_id, field, base.get(field), cur.get(field)))
        diffs.extend(_diff_scalar(sample_id, "report", base.get("report"), cur.get("report")))
        diffs.extend(
            _diff_scalar(sample_id, "volume_defs", base.get("volume_defs"), cur.get("volume_defs"))
        )
        diffs.extend(
            _diff_trace(
                sample_id,
                base.get("decision_trace") or {},
                cur.get("decision_trace") or {},
            )
        )
        diffs.extend(
            _diff_items(sample_id, base.get("items") or [], cur.get("items") or [])
        )
        diffs.extend(_diff_ledger(sample_id, base.get("ledger"), cur.get("ledger")))
        diffs.extend(
            _diff_confirm(sample_id, base.get("confirm_ledger"), cur.get("confirm_ledger"))
        )
    return diffs


def fingerprint(diffs: list[str]) -> list[tuple[str, int]]:
    """失败指纹分桶：把逐条差异归纳为"哪一类回归"，先给结论再给明细。

    桶的划分对齐历史复发链分类（表格解析/卷归属/总分对账/作用域），
    使报告首屏就能回答"这次像哪一类老问题"。
    """
    buckets: dict[str, int] = {}

    def bump(name: str) -> None:
        buckets[name] = buckets.get(name, 0) + 1

    for line in diffs:
        if "确认页账本 status" in line or "确认页账本 basis" in line:
            bump("确认页账本结论改变（对账状态/基准层级）")
        elif "确认页账本" in line or "confirm_db_reads" in line:
            bump("确认页账本其他字段漂移")
        elif "账本 status" in line or "账本 basis" in line:
            bump("账本结论改变（对账状态/基准层级）")
        elif "账本 group_rows" in line or "账本 scope_reports" in line:
            bump("账本明细行改变（组级/scope 级）")
        elif "账本 " in line or "ledger_calls" in line:
            bump("账本其他字段漂移")
        elif "裁决分支 PARSE_ROUTE/" in line:
            bump("解析路由改变（表被当作另一类表解析）")
        elif "裁决分支 VOLUME/" in line:
            bump("卷归属裁决改变")
        elif "裁决分支 ROW/" in line:
            bump("行级成项/丢弃改变")
        elif "裁决分支 TOTALS/" in line:
            bump("合计对账口径改变")
        elif "字段 score_weight" in line or "字段 volume_max" in line:
            bump("分值漂移")
        elif "字段 volume" in line or "字段 lot" in line or "字段 scheme" in line:
            bump("归属字段漂移（卷/包/方案）")
        elif "字段 source_pages" in line:
            bump("来源页码漂移")
        elif "条目数变化" in line or "item_count" in line:
            bump("条目数变化（漏提/多提）")
        elif "report:" in line:
            bump("合计报告变化")
        else:
            bump("其他字段漂移")
    return sorted(buckets.items(), key=lambda kv: -kv[1])


def main() -> int:
    if not BASELINE_PATH.exists():
        print(
            f"基线缺失: {BASELINE_PATH}\n"
            "首次建立请执行: .venv\\Scripts\\python.exe backend/scripts/scoring_snapshot.py --write",
            file=sys.stderr,
        )
        return 2

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = asyncio.run(build_snapshot())
    diffs = diff_snapshots(baseline, current)

    if not diffs:
        total_items = sum(s["item_count"] for s in current["samples"].values())
        print(
            f"T0 零差分: samples={current['sample_count']} items={total_items}"
        )
        return 0

    print(f"T0 差分 {len(diffs)} 处。失败指纹:", file=sys.stderr)
    for name, count in fingerprint(diffs):
        print(f"  - {name}: {count} 处", file=sys.stderr)
    print(f"\n明细（仅显示前 {MAX_REPORTED} 条）:", file=sys.stderr)
    for line in diffs[:MAX_REPORTED]:
        print(f"  {line}", file=sys.stderr)
    if len(diffs) > MAX_REPORTED:
        print(f"  ... 另有 {len(diffs) - MAX_REPORTED} 处", file=sys.stderr)
    print(
        "\n若这是有意变更：先确认每条差异都被业务裁定为正确，"
        "再执行 scoring_snapshot.py --write 并**单独提交**基线更新。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

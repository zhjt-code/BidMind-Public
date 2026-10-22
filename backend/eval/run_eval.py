"""AI 评测脚本

对比 AI 输出与黄金测试集标注，计算 Precision/Recall/F1。
当前支持:
  - tender_extract
  - collusion_extract
  - collusion_pair
  - collusion_project

用法:
    python -m eval.run_eval --dataset eval/golden_datasets/ --purpose tender_extract
    python -m eval.run_eval --dataset eval/golden_datasets/ --purpose collusion_pair
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_golden_datasets(dataset_dir: str, purpose: str | None = None) -> list[dict]:
    """加载黄金测试集"""
    datasets = []
    dataset_path = Path(dataset_dir)
    if not dataset_path.exists():
        print(f"数据集目录不存在: {dataset_dir}")
        return datasets

    for f in sorted(dataset_path.glob("*.json")):
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            if purpose and data.get("purpose") != purpose:
                continue
            datasets.append(data)

    print(f"加载 {len(datasets)} 个测试用例 (purpose={purpose or 'all'})")
    return datasets


def compute_requirement_metrics(
    predicted: list[dict], expected: list[dict]
) -> dict[str, float]:
    """计算招标要求提取的 P/R/F1

    匹配规则: category 相同 且 description 文本相似度 > 0.7
    （简化版: 使用子串包含判定）
    """
    matched = 0
    for exp in expected:
        exp_desc = exp.get("description", "")
        exp_cat = exp.get("category", "")
        for pred in predicted:
            pred_desc = pred.get("description", "")
            pred_cat = pred.get("category", "")
            if pred_cat == exp_cat and (
                exp_desc[:20] in pred_desc or pred_desc[:20] in exp_desc
            ):
                matched += 1
                break

    precision = matched / len(predicted) if predicted else 0.0
    recall = matched / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {"precision": precision, "recall": recall, "f1": f1, "matched": matched}


def compute_token_metrics(
    predicted: set[str],
    expected: set[str],
) -> dict[str, float]:
    """计算基于离散 token 集合的 P/R/F1。"""
    matched = len(predicted & expected)
    precision = matched / len(predicted) if predicted else 0.0
    recall = matched / len(expected) if expected else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1, "matched": matched}


def _norm_text(value: Any) -> str:
    """轻量文本归一化，避免标点/大小写/空白影响评测。"""
    text = str(value or "").strip().lower()
    for old, new in (
        ("（", "("),
        ("）", ")"),
        ("：", ":"),
        ("，", ","),
        ("；", ";"),
        ("\n", " "),
        ("\r", " "),
        ("\t", " "),
    ):
        text = text.replace(old, new)
    return " ".join(text.split())


def _normalize_money(value: Any) -> str:
    """报价字段归一化，仅用于离线样本对齐。"""
    text = _norm_text(value)
    return text.replace(",", "").replace("元", "").replace("人民币", "")


def _flatten_collusion_extract_tokens(payload: dict[str, Any]) -> set[str]:
    """将串标提取结果压平为 token 集合，便于做离线回归评测。"""
    tokens: set[str] = set()
    for doc in payload.get("documents", []):
        doc_id = str(doc.get("document_id") or doc.get("doc_id") or "").strip()
        if not doc_id:
            continue
        company = _norm_text(doc.get("company_name"))
        if company:
            tokens.add(f"{doc_id}|company|{company}")
        legal = _norm_text(doc.get("legal_representative"))
        if legal:
            tokens.add(f"{doc_id}|legal|{legal}")
        phone = _norm_text(doc.get("contact_phone"))
        if phone:
            tokens.add(f"{doc_id}|phone|{phone}")
        address = _norm_text(doc.get("registered_address"))
        if address:
            tokens.add(f"{doc_id}|address|{address}")
        credit = _norm_text(doc.get("unified_social_credit_code"))
        if credit:
            tokens.add(f"{doc_id}|credit|{credit}")
        price = _normalize_money(doc.get("total_price"))
        if price:
            tokens.add(f"{doc_id}|price|{price}")

        for name in doc.get("person_names", []):
            norm = _norm_text(name)
            if norm:
                tokens.add(f"{doc_id}|person|{norm}")
        for cert in doc.get("certificate_numbers", []):
            norm = _norm_text(cert)
            if norm:
                tokens.add(f"{doc_id}|cert|{norm}")
        for project in doc.get("past_projects", []):
            norm = _norm_text(project)
            if norm:
                tokens.add(f"{doc_id}|project|{norm}")
    return tokens


def _flatten_collusion_pair_tokens(payload: dict[str, Any]) -> set[str]:
    """将 pair 级黄金标注压平成 token 集合。"""
    tokens: set[str] = set()
    for pair in payload.get("pairs", []):
        pair_id = str(pair.get("pair_id") or "").strip()
        if not pair_id:
            continue
        for key in ("overall_risk", "risk_level", "decision_state", "review_state"):
            value = _norm_text(pair.get(key))
            if value:
                tokens.add(f"{pair_id}|{key}|{value}")
        for dim in pair.get("hit_dimensions", []):
            norm = _norm_text(dim).upper()
            if norm:
                tokens.add(f"{pair_id}|dim|{norm}")
    return tokens


def _flatten_collusion_project_tokens(payload: dict[str, Any]) -> set[str]:
    """将项目级黄金标注压平成 token 集合。"""
    tokens: set[str] = set()
    for key in ("project_decision", "project_risk_level", "overall_risk"):
        value = _norm_text(payload.get(key))
        if value:
            tokens.add(f"project|{key}|{value}")

    if "review_required" in payload:
        tokens.add(f"project|review_required|{bool(payload.get('review_required'))}")

    for group in payload.get("suspected_groups", []):
        if isinstance(group, dict):
            members = sorted(str(x) for x in group.get("document_ids", []))
            risk = _norm_text(group.get("risk_level"))
        else:
            members = sorted(str(x) for x in group)
            risk = ""
        if members:
            member_key = ",".join(members)
            tokens.add(f"project|group|{member_key}")
            if risk:
                tokens.add(f"project|group_risk|{member_key}|{risk}")
    return tokens


def run_tender_extract_eval(datasets: list[dict]) -> dict[str, Any]:
    """招标要求提取评测（离线模式: 仅对比已有输出与标注）"""
    results = []
    for ds in datasets:
        expected = ds.get("expected_output", {}).get("requirements", [])
        predicted = ds.get("actual_output", {}).get("requirements", [])

        if not predicted:
            print(f"  [{ds['id']}] 无 actual_output，跳过（请先运行 AI 提取并写入 actual_output 字段）")
            continue

        metrics = compute_requirement_metrics(predicted, expected)
        results.append({"id": ds["id"], **metrics})
        print(
            f"  [{ds['id']}] P={metrics['precision']:.2f} R={metrics['recall']:.2f} "
            f"F1={metrics['f1']:.2f} (matched={metrics['matched']}/{len(expected)})"
        )

    if not results:
        return {"avg_precision": 0, "avg_recall": 0, "avg_f1": 0, "count": 0}

    avg_p = sum(r["precision"] for r in results) / len(results)
    avg_r = sum(r["recall"] for r in results) / len(results)
    avg_f1 = sum(r["f1"] for r in results) / len(results)

    return {
        "avg_precision": avg_p,
        "avg_recall": avg_r,
        "avg_f1": avg_f1,
        "count": len(results),
        "details": results,
    }


def _run_token_eval(
    datasets: list[dict],
    *,
    expected_key: str,
    actual_key: str,
    flattener,
) -> dict[str, Any]:
    """通用 token 评测执行器。"""
    results = []
    for ds in datasets:
        expected = flattener(ds.get("expected_output", {}).get(expected_key, {}))
        predicted = flattener(ds.get("actual_output", {}).get(actual_key, {}))

        if not predicted:
            print(
                f"  [{ds['id']}] 无 actual_output.{actual_key}，"
                "跳过（请先写入离线预测结果）"
            )
            continue

        metrics = compute_token_metrics(predicted, expected)
        results.append({"id": ds["id"], **metrics})
        print(
            f"  [{ds['id']}] P={metrics['precision']:.2f} "
            f"R={metrics['recall']:.2f} F1={metrics['f1']:.2f} "
            f"(matched={metrics['matched']}/{len(expected)})"
        )

    if not results:
        return {"avg_precision": 0, "avg_recall": 0, "avg_f1": 0, "count": 0}

    avg_p = sum(r["precision"] for r in results) / len(results)
    avg_r = sum(r["recall"] for r in results) / len(results)
    avg_f1 = sum(r["f1"] for r in results) / len(results)
    return {
        "avg_precision": avg_p,
        "avg_recall": avg_r,
        "avg_f1": avg_f1,
        "count": len(results),
        "details": results,
    }


def run_collusion_extract_eval(datasets: list[dict]) -> dict[str, Any]:
    """串标提取评测：比较结构化提取关键字段。"""
    return _run_token_eval(
        datasets,
        expected_key="collusion_extract",
        actual_key="collusion_extract",
        flattener=_flatten_collusion_extract_tokens,
    )


def run_collusion_pair_eval(datasets: list[dict]) -> dict[str, Any]:
    """串标 pair 级评测：比较风险、决策状态与命中维度。"""
    return _run_token_eval(
        datasets,
        expected_key="collusion_pair",
        actual_key="collusion_pair",
        flattener=_flatten_collusion_pair_tokens,
    )


def run_collusion_project_eval(datasets: list[dict]) -> dict[str, Any]:
    """串标项目级评测：比较项目结论与嫌疑群组。"""
    return _run_token_eval(
        datasets,
        expected_key="collusion_project",
        actual_key="collusion_project",
        flattener=_flatten_collusion_project_tokens,
    )


def _flatten_collusion_rule_tokens(payload: dict[str, Any]) -> set[str]:
    """将规则注册表评测结果压平为 token 集合，用于 collusion_rule 口径的离线回归评测。

    payload 结构:
      rule_hits: 命中规则列表，每条含 pair_id / rule_id / level（可选）
      ledger:    情形账本，每条含 pair_id / rule_id / status（可核查性状态）
      gate:      闸门结论，每条含 pair_id / blocked（布尔）

    缺少 pair_id 或 rule_id 的条目静默跳过，保持与其他 flattener 一致的容错策略。
    """
    tokens: set[str] = set()

    # rule_hits: 命中规则；含非空 level 时额外产出带层级的 token
    for item in payload.get("rule_hits", []):
        pair_id = str(item.get("pair_id") or "").strip()
        rule_id = str(item.get("rule_id") or "").strip()
        if not pair_id or not rule_id:
            continue
        tokens.add(f"{pair_id}|hit|{rule_id}")
        level = _norm_text(item.get("level"))
        if level:
            tokens.add(f"{pair_id}|hit_level|{rule_id}|{level}")

    # ledger: 情形账本（可核查性状态）；status 缺失时跳过
    for item in payload.get("ledger", []):
        pair_id = str(item.get("pair_id") or "").strip()
        rule_id = str(item.get("rule_id") or "").strip()
        if not pair_id or not rule_id:
            continue
        status = _norm_text(item.get("status"))
        if not status:
            continue
        tokens.add(f"{pair_id}|ledger|{rule_id}|{status}")

    # gate: 闸门状态（pair 级阻断标志）；缺 pair_id 时跳过
    for item in payload.get("gate", []):
        pair_id = str(item.get("pair_id") or "").strip()
        if not pair_id:
            continue
        blocked = bool(item.get("blocked"))
        tokens.add(f"{pair_id}|gate_blocked|{blocked}")

    return tokens


def run_collusion_rule_eval(datasets: list[dict]) -> dict[str, Any]:
    """串标规则注册表评测：比较规则命中、情形账本状态与闸门结论。

    评测口径 collusion_rule，对应「电子档串标规则注册表」引入后的
    pair 级情形账本机制，用于阶段回归校验。
    """
    return _run_token_eval(
        datasets,
        expected_key="collusion_rule",
        actual_key="collusion_rule",
        flattener=_flatten_collusion_rule_tokens,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BidMind AI 评测脚本")
    parser.add_argument(
        "--dataset", default="eval/golden_datasets/", help="黄金测试集目录"
    )
    parser.add_argument(
        "--purpose",
        choices=[
            "tender_extract",
            "bid_extract",
            "review",
            "collusion_extract",
            "collusion_pair",
            "collusion_project",
            "collusion_rule",
        ],
        default=None,
        help="评测类型筛选",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("BidMind AI 评测")
    print("=" * 60)

    datasets = load_golden_datasets(args.dataset, args.purpose)
    if not datasets:
        print("无可用测试用例，退出")
        sys.exit(1)

    purpose = args.purpose or datasets[0].get("purpose", "tender_extract")

    if purpose == "tender_extract":
        summary = run_tender_extract_eval(datasets)
    elif purpose == "collusion_extract":
        summary = run_collusion_extract_eval(datasets)
    elif purpose == "collusion_pair":
        summary = run_collusion_pair_eval(datasets)
    elif purpose == "collusion_project":
        summary = run_collusion_project_eval(datasets)
    elif purpose == "collusion_rule":
        summary = run_collusion_rule_eval(datasets)
    else:
        print(f"评测类型 '{purpose}' 暂未实现，待后续扩展")
        sys.exit(0)

    print()
    print("-" * 60)
    print(f"汇总 ({summary['count']} 个用例):")
    print(f"  平均 Precision: {summary['avg_precision']:.3f}")
    print(f"  平均 Recall:    {summary['avg_recall']:.3f}")
    print(f"  平均 F1:        {summary['avg_f1']:.3f}")
    print("=" * 60)


if __name__ == "__main__":
    main()

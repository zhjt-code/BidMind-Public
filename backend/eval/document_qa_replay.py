"""文档质检离线评估装置

职责: 对投标 PDF 离线执行完整质检链(解析 -> 构模 -> 检查), 输出发现项 JSON;
      提供标注文件时, 按标注 verdict(tp/fp) 对照发现项, 输出分组精确率报告
不做: 不落库、不调 LLM、不修改检查器、不做信封收敛

用法(在 backend 目录下, 使用项目 .venv):
    python -m eval.document_qa_replay --pdf "..\\storage\\CK投标2.pdf"
    python -m eval.document_qa_replay --pdf <path> --labels eval/document_qa/CK投标2.labels.yaml

输出:
    eval/document_qa/<pdf名>_findings.json   全量发现项(供标注与复跑对比)
    eval/document_qa/<pdf名>_report.json     有标注时的对照报告
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

_OUT_DIR = Path(__file__).parent / "document_qa"

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """折叠空白, 供摘录前缀匹配。"""
    return _WS_RE.sub("", (text or "").strip())


def run_quality_checks(pdf_path: Path) -> dict[str, Any]:
    """执行离线质检链, 返回可序列化结果(与在线 stage 同一条核心链)。"""
    from app.core.config import settings
    from app.core.document_qa.analyzer import analyze_document, findings_to_dicts
    from app.core.document_qa.contracts import RULESET_VERSION
    from app.core.document_qa.model_builder import build_document_model
    from app.core.document_qa.rules.loader import load_rules
    from app.core.parser.pdf_parser import PdfParser

    ruleset_version = str(
        getattr(settings, "DOCUMENT_QA_RULESET_VERSION", "") or RULESET_VERSION
    )

    started = time.monotonic()
    parsed_doc = PdfParser().parse(pdf_path)
    parse_seconds = round(time.monotonic() - started, 1)

    model = build_document_model(
        parsed_doc,
        str(pdf_path),
        document_id=0,
        bid_part=None,
        max_span_pages=800,
    )
    analysis = analyze_document(model, load_rules())
    rows = findings_to_dicts(analysis.findings, document_id=0, bid_part=None)
    return {
        "document": pdf_path.name,
        "page_count": model.page_count,
        "ruleset_version": ruleset_version,
        "degraded": analysis.degraded,
        "degraded_reason": model.degraded_reason,
        "checker_status": analysis.checker_status,
        "parse_seconds": parse_seconds,
        "analyze_seconds": round(time.monotonic() - started - parse_seconds, 1),
        "findings": rows,
    }


def load_labels(path: Path) -> list[dict[str, Any]]:
    """读取标注文件。契约: labels[].{rule_code, page, excerpt, verdict(tp|fp), source?, note?}"""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    labels = list(data.get("labels") or [])
    for idx, label in enumerate(labels):
        if label.get("verdict") not in ("tp", "fp"):
            raise ValueError(f"labels[{idx}] verdict 必须是 tp/fp: {label}")
        if not label.get("rule_code") or not label.get("excerpt"):
            raise ValueError(f"labels[{idx}] 缺少 rule_code/excerpt: {label}")
    return labels


def _finding_pages(finding: dict[str, Any]) -> set[int]:
    items = ((finding.get("evidence") or {}).get("items")) or []
    return {int(item["page"]) for item in items if item.get("page") is not None}


def _finding_excerpts(finding: dict[str, Any]) -> list[str]:
    items = ((finding.get("evidence") or {}).get("items")) or []
    texts = [str(item.get("excerpt") or "") for item in items]
    texts.append(str(finding.get("message") or ""))
    return [_normalize(t) for t in texts if t]


def _matches(finding: dict[str, Any], label: dict[str, Any]) -> bool:
    if finding.get("rule_code") != label.get("rule_code"):
        return False
    page = label.get("page")
    if page is not None and int(page) not in _finding_pages(finding):
        return False
    needle = _normalize(str(label["excerpt"]))
    return any(needle in hay for hay in _finding_excerpts(finding))


def match_findings_to_labels(
    findings: list[dict[str, Any]],
    labels: list[dict[str, Any]],
) -> dict[str, Any]:
    """一对一贪心匹配, 产出对照报告结构。

    分类: tp_hit(真阳保留) / fp_hit(误报仍在) / unlabeled(新发现待定性)
          missing_tp(真阳丢失, 回归警报) / missing_fp(误报已消除)
    """
    consumed: set[int] = set()
    tp_hit: list[dict[str, Any]] = []
    fp_hit: list[dict[str, Any]] = []
    missing_tp: list[dict[str, Any]] = []
    missing_fp: list[dict[str, Any]] = []

    for label in labels:
        hit_index: Optional[int] = None
        for idx, finding in enumerate(findings):
            if idx in consumed:
                continue
            if _matches(finding, label):
                hit_index = idx
                break
        if hit_index is None:
            (missing_tp if label["verdict"] == "tp" else missing_fp).append(label)
            continue
        consumed.add(hit_index)
        entry = {"label": label, "finding_index": hit_index}
        (tp_hit if label["verdict"] == "tp" else fp_hit).append(entry)

    unlabeled = [f for i, f in enumerate(findings) if i not in consumed]
    labeled_total = len(tp_hit) + len(fp_hit)
    precision = (len(tp_hit) / labeled_total) if labeled_total else None

    by_rule: dict[str, dict[str, int]] = {}
    for bucket, name in ((tp_hit, "tp_hit"), (fp_hit, "fp_hit")):
        for entry in bucket:
            code = entry["label"]["rule_code"]
            by_rule.setdefault(code, {"tp_hit": 0, "fp_hit": 0, "unlabeled": 0})[name] += 1
    for finding in unlabeled:
        code = str(finding.get("rule_code"))
        by_rule.setdefault(code, {"tp_hit": 0, "fp_hit": 0, "unlabeled": 0})["unlabeled"] += 1

    return {
        "labeled_precision": precision,
        "counts": {
            "findings_total": len(findings),
            "labels_total": len(labels),
            "tp_hit": len(tp_hit),
            "fp_hit": len(fp_hit),
            "unlabeled": len(unlabeled),
            "missing_tp": len(missing_tp),
            "missing_fp": len(missing_fp),
        },
        "by_rule": by_rule,
        "missing_tp": missing_tp,
        "missing_fp": missing_fp,
        "unlabeled": [
            {
                "rule_code": f.get("rule_code"),
                "pages": sorted(_finding_pages(f)),
                "title": f.get("title"),
                "message": (f.get("message") or "")[:120],
            }
            for f in unlabeled
        ],
    }


def render_summary(result: dict[str, Any], report: Optional[dict[str, Any]]) -> str:
    lines = [
        f"文档: {result['document']}  页数: {result['page_count']}  "
        f"规则版本: {result['ruleset_version']}  degraded: {result['degraded']}",
        f"耗时: 解析 {result['parse_seconds']}s + 质检 {result['analyze_seconds']}s",
        f"发现项: {len(result['findings'])} 条",
    ]
    per_rule: dict[str, int] = {}
    for finding in result["findings"]:
        code = str(finding.get("rule_code"))
        per_rule[code] = per_rule.get(code, 0) + 1
    for code in sorted(per_rule):
        lines.append(f"  {code}: {per_rule[code]}")
    if report is not None:
        counts = report["counts"]
        precision = report["labeled_precision"]
        lines.append(
            f"对照: tp_hit={counts['tp_hit']} fp_hit={counts['fp_hit']} "
            f"unlabeled={counts['unlabeled']} missing_tp={counts['missing_tp']} "
            f"missing_fp={counts['missing_fp']}",
        )
        lines.append(
            "已定性精确率: " + (f"{precision:.1%}" if precision is not None else "无已定性命中"),
        )
        if counts["missing_tp"]:
            lines.append("警报: 有标注为真阳的发现未复现(可能回归), 见 report.missing_tp")
    return "\n".join(lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="文档质检离线评估")
    parser.add_argument("--pdf", required=True, help="投标 PDF 路径")
    parser.add_argument("--labels", default=None, help="标注文件 labels.yaml 路径(可选)")
    parser.add_argument("--out", default=None, help="findings 输出路径(默认 eval/document_qa/)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF 不存在: {pdf_path}")
        return 2

    result = run_quality_checks(pdf_path)

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else _OUT_DIR / f"{pdf_path.stem}_findings.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    report: Optional[dict[str, Any]] = None
    if args.labels:
        labels_path = Path(args.labels)
        if not labels_path.exists():
            print(f"标注文件不存在: {labels_path}")
            return 2
        report = match_findings_to_labels(result["findings"], load_labels(labels_path))
        report_path = _OUT_DIR / f"{pdf_path.stem}_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"[输出] 对照报告: {report_path}")

    print(f"[输出] 发现项: {out_path}")
    print(render_summary(result, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())

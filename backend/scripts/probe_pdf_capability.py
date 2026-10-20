"""chat直传PDF能力探针。

职责: 用1页带验证码的微型PDF, 实测 review 池各通道(含 DashScope 文档模型
      派生目标)对四方言的支持情况并输出矩阵; --apply 时把首个验证通过的
      方言写入对应配置的能力档案 pdf_file_input 字段。
不做: 不修改调度池/模型选择、不参与生产调用链、除能力档案外不写任何配置。

用法(PowerShell):
  cd "h:\\BidMind AI Lite\\backend"
  ..\\.venv\\Scripts\\python.exe scripts\\probe_pdf_capability.py            # 只读探测
  ..\\.venv\\Scripts\\python.exe scripts\\probe_pdf_capability.py --apply    # 探测并写入能力档案

池子变更(新增Kimi/阶跃等通道)后重跑一次即可刷新能力档案, 无需改代码。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import shutil
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

VERIFY_CODE = "BIDMIND-7391"
PROBE_TIMEOUT_S = 90.0
# 已知具备 files API(file-extract) 的厂商主机片段
_EXTRACT_TEXT_HOSTS = ("moonshot", "stepfun", "kimi")


def _build_probe_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 100),
        f"能力探针验证页 验证码: {VERIFY_CODE}",
        fontname="china-s",
        fontsize=14,
    )
    data = doc.tobytes()
    doc.close()
    return data


def _candidate_dialects(api_base: str) -> list[str]:
    """按主机推断值得一试的方言顺序(避免对明显不支持的端点浪费调用)。"""
    host = api_base.lower()
    dialects = ["openai_file", "ark_file_url"]
    if any(hint in host for hint in _EXTRACT_TEXT_HOSTS):
        dialects.append("extract_text")
    return dialects


async def _probe_target(target, pdf_bytes: bytes, pdf_base64: str) -> tuple[str, str]:
    """探测单目标; 返回 (结论, 详情)。结论: ok / unsupported / no_evidence / error。"""
    from app.core.ai.pdf_transport import PdfInputUnsupported, chat_with_pdf

    try:
        text = await asyncio.wait_for(
            chat_with_pdf(
                target,
                pdf_bytes=pdf_bytes,
                pdf_base64=pdf_base64,
                filename="bidmind_probe.pdf",
                system_message="你是文档读取助手, 只输出从附件中读到的内容。",
                user_message=(
                    "请读取附件PDF, 原样输出其中'验证码:'后面的完整验证码, "
                    "不要输出其他内容。"
                ),
                temperature=0.0,
                # 思考型模型(kimi-k3等)的推理输出会吃掉预算, 过小会导致
                # 正文为空被误判 no_evidence
                max_tokens=2000,
            ),
            timeout=PROBE_TIMEOUT_S,
        )
    except PdfInputUnsupported as exc:
        return "unsupported", str(exc)[:120]
    except Exception as exc:  # noqa: BLE001 - 探针逐目标容错
        return "error", f"{type(exc).__name__}: {str(exc)[:120]}"
    if "7391" in (text or ""):
        return "ok", (text or "").strip()[:60]
    # 调用被接受但读不到验证码 = 文件实际未被模型消费, 视同不支持
    return "no_evidence", (text or "").strip()[:60]


async def main() -> None:
    parser = argparse.ArgumentParser(description="chat直传PDF能力探针")
    parser.add_argument(
        "--apply", action="store_true",
        help="把首个验证通过的方言写入能力档案 pdf_file_input",
    )
    parser.add_argument(
        "--purpose", default="scoring",
        help="探测的用途池(默认 scoring 评分专用池, 池空自动回退 review)",
    )
    args = parser.parse_args()

    from app.core.ai.pdf_transport import PdfTransportTarget, _host
    from app.core.ai_settings import load_ai_settings, save_ai_settings
    from app.core.scoring_pdf_rescue_config import scoring_pdf_rescue_settings as cfg

    pdf_bytes = _build_probe_pdf()
    pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")

    settings_model = load_ai_settings()
    purpose = args.purpose
    configs = settings_model.get_configs(purpose)
    if not configs and purpose == "scoring":
        print("scoring 评分专用池为空, 回退探测 review 池(与运行时回退逻辑一致)")
        purpose = "review"
        configs = settings_model.get_configs(purpose)
    pool_ids = set(settings_model.get_pool_ids(purpose))
    verified: dict[str, str] = {}  # config_id -> dialect
    rows: list[tuple[str, str, str, str]] = []

    for config in configs:
        if pool_ids and config.id not in pool_ids:
            continue
        keys = config.get_all_keys()
        if not keys:
            rows.append((config.model_name, _host(config.api_base), "-", "无可用Key, 跳过"))
            continue
        for dialect in _candidate_dialects(config.api_base):
            target = PdfTransportTarget(
                config_id=config.id,
                label=f"{config.model_name}@{_host(config.api_base)}",
                api_base=config.api_base, api_key=keys[0],
                model_name=config.model_name,
                dialect=dialect, timeout=PROBE_TIMEOUT_S,
            )
            verdict, detail = await _probe_target(target, pdf_bytes, pdf_base64)
            rows.append((config.model_name, _host(config.api_base), dialect, f"{verdict} | {detail}"))
            if verdict == "ok":
                verified[config.id] = dialect
                break

    # DashScope 派生目标(文档模型覆写, 运行时自动派生, 不写能力档案)
    for config in configs:
        if pool_ids and config.id not in pool_ids:
            continue
        if "dashscope" not in _host(config.api_base) or not config.get_all_keys():
            continue
        doc_model = cfg.SCORING_PDF_RESCUE_DASHSCOPE_MODEL
        target = PdfTransportTarget(
            config_id=f"{config.id}#dashscope_doc",
            label=f"{doc_model}@{_host(config.api_base)}",
            api_base=config.api_base, api_key=config.get_all_keys()[0],
            model_name=doc_model, dialect="dashscope_fileid",
            timeout=PROBE_TIMEOUT_S,
        )
        verdict, detail = await _probe_target(target, pdf_bytes, pdf_base64)
        rows.append((f"{doc_model}(派生)", _host(config.api_base), "dashscope_fileid", f"{verdict} | {detail}"))
        break

    print("\n=== chat直传PDF能力矩阵 ===")
    for model, host, dialect, result in rows:
        print(f"{model:28s} {host:36s} {dialect:18s} {result}")

    if args.apply and verified:
        from app.core.ai_settings import SETTINGS_FILE

        backup = SETTINGS_FILE.with_suffix(".json.bak_pdf_probe")
        if SETTINGS_FILE.exists() and not backup.exists():
            shutil.copy2(SETTINGS_FILE, backup)
            print(f"\n能力档案写入前已备份: {backup}")
        from app.core.ai_capabilities import ModelCapabilityProfile

        current = load_ai_settings()
        for config in current.get_configs(purpose):
            if config.id in verified:
                if config.capabilities is None:
                    config.capabilities = ModelCapabilityProfile()
                config.capabilities.pdf_file_input = verified[config.id]
        await save_ai_settings(current)
        print(f"已写入能力档案: { {cid: d for cid, d in verified.items()} }")
    elif args.apply:
        print("\n无验证通过的方言, 能力档案未改动")


if __name__ == "__main__":
    asyncio.run(main())

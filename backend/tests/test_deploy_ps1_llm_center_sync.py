"""deploy.ps1 必须远端执行 LLM Center 表导入，不能只打印 Next。

锁定 ：上传快照后强制 import + 重启；禁止把 auto_publish 当作对齐手段。
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_PS1 = _REPO_ROOT / "deploy.ps1"


def _deploy_src() -> str:
    return _DEPLOY_PS1.read_text(encoding="utf-8")


def test_deploy_ps1_defines_and_calls_remote_llm_center_import() -> None:
    """快照上传成功后必须调用 Import-LlmCenterRegistry，失败则整次部署失败。"""
    src = _deploy_src()
    assert "function Import-LlmCenterRegistry" in src
    after_upload = src.split("if (Deploy-LlmCenterRegistry)", 1)[1]
    assert "Import-LlmCenterRegistry" in after_upload
    assert "$success = $false" in after_upload.split("Import-LlmCenterRegistry", 1)[1][:800]


def test_import_function_copies_local_script_then_imports_and_restarts() -> None:
    """import 必须 docker cp 本地脚本再执行，避免容器内旧脚本踩。"""
    src = _deploy_src()
    start = src.index("function Import-LlmCenterRegistry")
    body = src[start: src.index("# ==================== Main")]
    assert "docker cp" in body
    assert "llm_center_registry_sync.py import" in body
    assert "--yes" in body
    assert "restart backend worker-process worker-llm worker-maintenance" in body
    assert "auto_publish" not in body


def test_deploy_next_steps_do_not_instruct_auto_publish() -> None:
    """Next 提示不得再让运维跑 auto_publish，否则会用代码草稿盖掉已导入的 db 定稿。"""
    src = _deploy_src()
    next_section = src[src.index("Next: SSH") :]
    assert "auto_publish_code_registry.py" not in next_section


def test_all_preserve_cloud_is_full_deploy_without_ai_or_llm_center() -> None:
    """-AllPreserveCloud 与 -AllNoAISettings 同部署面，但不传 ai_settings、不导入 LLM Center。"""
    src = _deploy_src()
    assert "[switch]$AllPreserveCloud" in src
    assert "$AllNoAISettings -or $AllPreserveCloud -or $All" in src
    assert "$Infra -or $AllNoAISettings -or $AllPreserveCloud -or $All" in src
    assert "$OCR -or $AllNoAISettings -or $AllPreserveCloud -or $All" in src
    assert "if ($AISettings -or $All)" in src
    assert "if ($AISettings -or $AllPreserveCloud" not in src
    assert "$AllPreserveCloud -and ($All -or $AISettings)" in src
    skip_marker = "Skipping LLM Center registry sync (-AllPreserveCloud)"
    assert skip_marker in src
    skip_at = src.index(skip_marker)
    deploy_at = src.index("if (Deploy-LlmCenterRegistry)")
    assert skip_at < deploy_at
    # -App 等路径仍走默认导入：跳过分支必须包在 AllPreserveCloud 判断内
    skip_block = src[src.rindex("if ($AllPreserveCloud)", 0, skip_at) : deploy_at]
    assert "Deploy-LlmCenterRegistry" not in skip_block


def test_all_preserve_cloud_next_steps_do_not_claim_llm_center_imported() -> None:
    """保留云端入口的 Next 不得声称已导入 LLM Center。"""
    src = _deploy_src()
    next_section = src[src.index("Next: SSH") :]
    preserve_hint = next_section[
        next_section.index("elseif ($AllPreserveCloud)") :
        next_section.index("elseif ($deployed -contains \"AISettings\")")
    ]
    assert "already imported" not in preserve_hint
    assert "left unchanged" in preserve_hint

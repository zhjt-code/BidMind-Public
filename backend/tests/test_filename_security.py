"""文件名安全工具防御测试

映射风险: 素材/文档/知识库上传文件名路径穿越与任意覆盖(对应 bug_tracking BUG)。
覆盖: 正常路径、穿越路径、保留名、不可见字符、超长、落点逃逸断言、导出文件名拼装。
"""

from pathlib import Path

import pytest

from app.core.filename import sanitize_filename, resolve_within_dir, build_export_filename


class TestSanitizeFilename:
    def test_keeps_normal_name(self):
        assert sanitize_filename("公司资质.pdf") == "公司资质.pdf"

    @pytest.mark.parametrize("payload", [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config",
        "/etc/shadow",
        "C:\\Windows\\evil.dll",
        "subdir/inner.pdf",
    ])
    def test_strips_path_components(self, payload):
        """穿越/带目录的文件名清洗后不得残留任何分隔符。"""
        result = sanitize_filename(payload)
        assert "/" not in result
        assert "\\" not in result
        assert not result.startswith("..")

    def test_pure_dotdot_falls_back(self):
        assert sanitize_filename("..") == "unnamed"
        assert sanitize_filename("...", fallback="x") == "x"

    def test_empty_uses_fallback(self):
        assert sanitize_filename("", fallback="material") == "material"
        assert sanitize_filename("   ", fallback="material") == "material"

    def test_windows_reserved_name_prefixed(self):
        assert sanitize_filename("CON.txt").startswith("_")
        assert sanitize_filename("nul").startswith("_")

    def test_invisible_unicode_replaced(self):
        result = sanitize_filename("a\u202eb.pdf")
        assert "\u202e" not in result

    def test_overlong_truncated_keeps_ext(self):
        name = "a" * 300 + ".pdf"
        result = sanitize_filename(name)
        assert len(result) <= 200
        assert result.endswith(".pdf")


class TestResolveWithinDir:
    def test_normal_lands_inside(self, tmp_path: Path):
        target = resolve_within_dir(tmp_path, "ok.pdf")
        assert target.parent == tmp_path.resolve()
        assert target.name == "ok.pdf"

    def test_traversal_raises(self, tmp_path: Path):
        """已清洗一般不会触发，但纵深防御必须拦截逃逸落点。"""
        with pytest.raises(ValueError):
            resolve_within_dir(tmp_path, "../escape.pdf")

    def test_absolute_escape_raises(self, tmp_path: Path):
        with pytest.raises(ValueError):
            resolve_within_dir(tmp_path, "/etc/passwd")

    def test_sanitized_payload_is_safe(self, tmp_path: Path):
        """清洗 + 落点校验组合: 穿越载荷最终安全落在目录内。"""
        safe = sanitize_filename("../../../etc/passwd")
        target = resolve_within_dir(tmp_path, safe)
        assert target.parent == tmp_path.resolve()


class TestBuildExportFilename:
    def test_three_flow_labels(self):
        assert build_export_filename("市政道路", "投标审查", "pdf") == "市政道路投标审查.pdf"
        assert build_export_filename("市政道路", "串标审查", "pdf") == "市政道路串标审查.pdf"
        assert build_export_filename("市政道路", "标书初稿", "docx") == "市政道路标书初稿.docx"

    def test_empty_name_uses_fallback(self):
        assert build_export_filename("", "投标审查", "pdf") == "未命名项目投标审查.pdf"
        assert build_export_filename("   ", "标书初稿", "docx") == "未命名项目标书初稿.docx"
        assert build_export_filename(None, "串标审查", "pdf") == "未命名项目串标审查.pdf"

    def test_illegal_chars_stripped_from_project_name(self):
        result = build_export_filename("公路<a>/b:c", "投标审查", "pdf")
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result
        assert "<" not in result
        assert result.endswith("投标审查.pdf")

    def test_does_not_keep_legacy_id_or_english_prefix(self):
        name = build_export_filename("示范项目", "标书初稿", "docx")
        assert "bid_document_" not in name
        assert not name.startswith("审查报告_")
        assert "示范项目" in name

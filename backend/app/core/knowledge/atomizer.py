"""知识库原子化旁路适配器。

该模块只复用解析/OCR/VL等底层能力并返回内存结构，不写入任何业务表，
避免影响已经稳定的写标素材处理主流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AtomizedMaterial:
    """单个可引用的知识库原子素材。"""

    title: str
    content: str
    page_start: int | None
    page_end: int | None
    atom_type: str = "page"
    business_type: str | None = None
    business_label: str | None = None
    summary: str | None = None
    confidence: int = 80


@dataclass(frozen=True)
class AtomizationResult:
    """知识库资料原子化结果。"""

    full_text: str
    page_texts: list[tuple[int, str]]
    page_count: int
    parsed_pages: list = field(default_factory=list)
    atoms: list[AtomizedMaterial] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolved_file_path: str | None = None


class KnowledgeAtomizer:
    """知识库资料原子化适配器。"""

    def __init__(self, *, enable_vl: bool = True) -> None:
        self.enable_vl = enable_vl

    async def atomize(
        self,
        *,
        file_path: Path,
        document_id: int,
        image_save_dir: Path,
        title: str,
        business_type: str,
        business_label: str | None = None,
    ) -> AtomizationResult:
        """解析资料并生成可引用原子。

        当前策略保持保守：按有效页面形成原子，无法拆分时回退为整份文档原子。
        Office 文档先经 ``normalize_office_path_for_parse`` 转 PDF，再走 PDF 主链。
        """
        suffix = file_path.suffix.lower()
        resolved_file_path: str | None = None
        if suffix in {".txt", ".md"}:
            full_text = file_path.read_text(encoding="utf-8", errors="ignore")
            page_texts = [(1, full_text)] if full_text.strip() else []
            page_count = 1
            parsed_pages = []
        else:
            from app.core.config import settings
            from app.core.convert.office_to_pdf import normalize_office_path_for_parse
            from app.core.writer.material_content_extractor import MaterialContentExtractor

            resolved_path, parse_ext = await normalize_office_path_for_parse(
                file_path,
                timeout_sec=int(settings.OFFICE_CONVERT_TIMEOUT_SEC),
            )
            resolved_file_path = str(resolved_path)
            extractor = MaterialContentExtractor(enable_vl=self.enable_vl)
            image_save_dir.mkdir(parents=True, exist_ok=True)
            parse_result = extractor.parse_only(
                file_path=resolved_file_path,
                ext=parse_ext,
                image_save_dir=image_save_dir,
            )
            if parse_result.images and self.enable_vl:
                await extractor.batch_vl_recognize(
                    parse_result.images,
                    project_id=0,
                )
            material_content = extractor.assemble_with_vl(parse_result)
            full_text = material_content.text or ""
            page_texts = material_content.page_texts or [(1, full_text)]
            page_count = material_content.page_count
            parsed_pages = material_content.parsed_pages

        atoms = self._build_page_atoms(
            title=title,
            page_texts=page_texts,
            full_text=full_text,
            business_type=business_type,
            business_label=business_label,
        )
        warnings: list[str] = []
        if not atoms and full_text.strip():
            atoms = [
                AtomizedMaterial(
                    title=title,
                    content=full_text,
                    page_start=1,
                    page_end=page_count or 1,
                    atom_type="document",
                    business_type=business_type,
                    business_label=business_label,
                    summary=self._summarize(full_text),
                    confidence=60,
                )
            ]
            warnings.append("未能按页面拆分，已生成整份资料原子")
        return AtomizationResult(
            full_text=full_text,
            page_texts=page_texts,
            page_count=page_count,
            parsed_pages=parsed_pages,
            atoms=atoms,
            warnings=warnings,
            resolved_file_path=resolved_file_path,
        )

    def _build_page_atoms(
        self,
        *,
        title: str,
        page_texts: list[tuple[int, str]],
        full_text: str,
        business_type: str,
        business_label: str | None,
    ) -> list[AtomizedMaterial]:
        atoms: list[AtomizedMaterial] = []
        clean_pages = [
            (page, text.strip())
            for page, text in page_texts
            if text and text.strip()
        ]
        if len(clean_pages) <= 1:
            text = clean_pages[0][1] if clean_pages else full_text.strip()
            if not text:
                return []
            return [
                AtomizedMaterial(
                    title=title,
                    content=text,
                    page_start=clean_pages[0][0] if clean_pages else 1,
                    page_end=clean_pages[0][0] if clean_pages else 1,
                    atom_type="document",
                    business_type=business_type,
                    business_label=business_label,
                    summary=self._summarize(text),
                    confidence=70,
                )
            ]

        for page, text in clean_pages:
            atom_title = self._infer_atom_title(title, page, text)
            atoms.append(
                AtomizedMaterial(
                    title=atom_title,
                    content=text,
                    page_start=page,
                    page_end=page,
                    atom_type="page",
                    business_type=business_type,
                    business_label=business_label,
                    summary=self._summarize(text),
                    confidence=80,
                )
            )
        return atoms

    @staticmethod
    def _infer_atom_title(title: str, page: int, text: str) -> str:
        for line in text.splitlines():
            clean = " ".join(line.strip().split())
            if 4 <= len(clean) <= 60:
                return clean
        return f"{title} - 第{page}页"

    @staticmethod
    def _summarize(text: str) -> str:
        clean = " ".join((text or "").split())
        return clean[:200]

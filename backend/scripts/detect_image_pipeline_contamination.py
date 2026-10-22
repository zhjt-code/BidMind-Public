"""排查图片链路历史污染风险

使用方法:
    cd backend
    python -m scripts.detect_image_pipeline_contamination
"""

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.chunk import Chunk
from app.models.collusion_extraction import CollusionExtraction
from app.models.document import Document, DocumentType
from app.models.project import Project


_ID_KEYS = (
    "id_number",
    "identity_number",
    "identity_card",
    "id_card",
    "身份证号",
    "公民身份号码",
)


def _extract_person_keys(personnel_info: object) -> set[str]:
    """从提取结果中抽取可用于交叉比对的人员键。"""
    if not isinstance(personnel_info, list):
        return set()

    keys: set[str] = set()
    for item in personnel_info:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        id_value = ""
        for key in _ID_KEYS:
            value = str(item.get(key) or "").strip()
            if value:
                id_value = value
                break
        if name and id_value:
            keys.add(f"{name}|{id_value}")
    return keys


async def main() -> None:
    upload_dir = Path(settings.UPLOAD_DIR)
    suspicious_projects: list[dict] = []

    async with AsyncSessionLocal() as db:
        project_rows = await db.execute(select(Project).order_by(Project.id))
        projects = list(project_rows.scalars().all())

        for project in projects:
            doc_rows = await db.execute(
                select(Document).where(Document.project_id == project.id)
            )
            documents = list(doc_rows.scalars().all())
            if len(documents) < 2:
                continue

            bid_docs = [d for d in documents if d.doc_type == DocumentType.BID]
            chunk_rows = await db.execute(
                select(Chunk.document_id, Chunk.chunk_type).where(
                    Chunk.document_id.in_([d.id for d in documents])
                )
            )
            chunk_stats: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for document_id, chunk_type in chunk_rows.fetchall():
                chunk_stats[int(document_id)][str(chunk_type or "text")] += 1

            extraction_rows = await db.execute(
                select(CollusionExtraction).where(
                    CollusionExtraction.project_id == project.id,
                    CollusionExtraction.extraction_status == "success",
                )
            )
            overlap_map: dict[str, list[int]] = defaultdict(list)
            for extraction in extraction_rows.scalars().all():
                for person_key in _extract_person_keys(extraction.personnel_info):
                    overlap_map[person_key].append(int(extraction.document_id))

            shared_people = {
                key: sorted(set(doc_ids))
                for key, doc_ids in overlap_map.items()
                if len(set(doc_ids)) > 1
            }

            legacy_image_dir = upload_dir / str(project.id) / "images"
            legacy_flat_files = []
            if legacy_image_dir.exists():
                legacy_flat_files = [
                    p.name for p in legacy_image_dir.iterdir()
                    if p.is_file() and p.name.startswith("p")
                ]

            if (
                legacy_flat_files
                or any(stats.get("image_evidence", 0) > 0 for stats in chunk_stats.values())
                or shared_people
            ):
                suspicious_projects.append({
                    "project_id": project.id,
                    "project_name": project.name,
                    "bid_doc_count": len(bid_docs),
                    "legacy_flat_image_files": legacy_flat_files[:20],
                    "chunk_stats": {str(k): dict(v) for k, v in chunk_stats.items()},
                    "shared_people": shared_people,
                })

    logger.info(f"共发现 {len(suspicious_projects)} 个需优先人工复核的项目")
    print(json.dumps(suspicious_projects, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

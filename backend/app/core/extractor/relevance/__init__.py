"""评审意义判定子包（P3 可评审性独立判定 pass）。

职责: 对合并后的招标要求整表批量判定 review_relevance（可评审性），并识别含分值评分项。
不做: 不做要求提取/合并（见 tender_extractor）、不做评分表结构化（见 extractor/scoring）。
"""

from app.core.extractor.relevance.relevance_classifier import classify_review_relevance

__all__ = ["classify_review_relevance"]

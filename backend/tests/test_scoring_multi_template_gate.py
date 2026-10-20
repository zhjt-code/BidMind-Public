"""多模板方案切分闸门判据 golden。

职责: 锁定"多套备选评分模板"的结构判据——模板编码锚点(行首、带数字后缀)≥2 且
 文档含"模板/细则"字样; 防止闸门退回单一文档字面量(FWJS)导致换名即失效。
不做: 不判断方案边界具体位置(交 table_stitcher._split_scheme_restarts 的
 "序号重启+前段闭合100"内部判据)、不改列角色推断、不调 LLM/DB。
"""

from __future__ import annotations
from tests.synthetic_data import synthetic_text

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from app.core.parser.scoring_chunk_sources import (  # noqa: E402
    has_multi_template_scheme_signal,
)

import pytest

pytestmark = pytest.mark.scoring


class TestMultiTemplateSchemeSignal:
    """判据的正例/负例边界（负例即误触发防线）。"""

    def test_sample09_style_anchors_fire(self):
        """sample09 形态: 编码带/不带冒号混排, 部分有 `N.` 前缀（不得回退）。"""
        texts = [
            "(1)技术评分标准\n5.FWJS05:技改大修、专业分包工程施工服务技术详评模板V1.0\n评审要素 评审内容",
            "FWJS16服务技术详评模板(通用)V1.0(综合服务、运维服务)\n评审要素 评审内容",
            "18.FWJS18科技技术服务、科技项目技术详评模板V1.0\n评审要素 评审内容",
        ]

        assert has_multi_template_scheme_signal(texts) is True

    def test_new1_style_anchors_fire(self):
        """模板库型招标全文: FWSW/JS-GC 双前缀共存, 编码前缀与 sample09 不同。"""
        texts = [
            "评标办法前附表之三:商务评分标准\nFWSW01:服务类通用商务详评细则\n项目 评审要素",
            "FWSW02:输变电工程设计类商务评分细则\n项目 评审要素",
            "3.JS-GCSJ01:输变电工程工程设计服务技术详评模板\n评审要素 评审内容",
        ]

        assert has_multi_template_scheme_signal(texts) is True

    def test_certification_acronyms_in_prose_do_not_fire(self):
        """sample22 误触发防线: 认证缩写是正文内容而非模板锚点, 且为纯字母无数字。"""
        texts = [
            "企业资质 具有SPCA或CMMI或CSMM软件能力成熟度等级证书,五级得3分,其他情况不得分。",
            "CCSC网络安全能力认证证书或注册信息安全专业人员(CISP)中任意一个证书得1分。",
            "本项评分细则见下表。",
        ]

        assert has_multi_template_scheme_signal(texts) is False

    def test_pure_letter_label_does_not_fire(self):
        """sample17 误触发防线: `TEL:` 这类纯字母标签不是模板编码。"""
        texts = [synthetic_text('联系人 <PERSON_A>\nTEL:', '010-', '1234', '5678', '\n本细则适用于本项目。'), synthetic_text('FAX:', '010-', '8765', '4321')]

        assert has_multi_template_scheme_signal(texts) is False

    def test_single_anchor_does_not_fire(self):
        """单套模板不构成多方案——闸门须要求 >=2 个不同编码。"""
        texts = ["1.FWJS05:输变电工程施工服务技术详评模板V1.0\n评审要素 评审内容"]

        assert has_multi_template_scheme_signal(texts) is False

    def test_anchors_without_template_term_do_not_fire(self):
        """缺"模板/细则"语境时不得仅凭编码开闸（编码也可能是产品型号）。"""
        texts = ["ABC01:设备型号一\n技术参数", "ABC02:设备型号二\n技术参数"]

        assert has_multi_template_scheme_signal(texts) is False

    def test_empty_input_does_not_fire(self):
        assert has_multi_template_scheme_signal([]) is False
        assert has_multi_template_scheme_signal(["", None]) is False  # type: ignore[list-item]

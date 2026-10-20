"""评分 VLM 视觉救援通道子包。

职责: 确定性链非绿时的视觉救援——按需渲染评分区页面送 vision 池 VLM,
      产物过锚定+闭合双门禁后原子替换救援尾部结果。
不做: 不修改确定性解析/救援尾部内部逻辑、不落库、不新增 DB Schema。
"""

from app.core.extractor.scoring.vlm.vlm_rescue_channel import attempt_vlm_scoring_rescue

__all__ = ["attempt_vlm_scoring_rescue"]

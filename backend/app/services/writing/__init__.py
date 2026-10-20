"""标书编写服务子包

按业务阶段拆分的 sub-service 模块集合。WritingService 入口仍位于
``app.services.writing_service``，对外契约 100% 兼容。

子模块清单（按 Task 5 拆分顺序）：

- :mod:`.progress_steps` 进度组装纯函数（Step 1）
- :mod:`.clause_service` 阶段 1 约束（Step 2）
- :mod:`.outline_service` 阶段 2 目录（Step 3）
- :mod:`.match_service` 阶段 3 匹配（Step 4）
- :mod:`.binding_service` 三元绑定（Step 5）
- :mod:`.compose_service` 阶段 4 内容生成（Step 6）
- :mod:`.render_service` 阶段 5 渲染输出（Step 7）
- :mod:`.material_service` 素材域（Step 8）
"""

from __future__ import annotations

from app.services.writing.binding_service import BindingService
from app.services.writing.clause_service import ClauseService
from app.services.writing.compose_service import ComposeService
from app.services.writing.match_service import MatchService
from app.services.writing.material_service import MaterialService
from app.services.writing.outline_service import OutlineService
from app.services.writing.render_service import RenderService

__all__ = [
    "BindingService",
    "ClauseService",
    "ComposeService",
    "MatchService",
    "MaterialService",
    "OutlineService",
    "RenderService",
]

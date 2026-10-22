"""SectionComposer 内部职责拆分子包

按 Task 6 拆分为 7 个纯函数模块，每个模块接收 ``composer: "SectionComposer"``
作为第一参数（用 ``TYPE_CHECKING`` 避免循环 import）。

模块清单（按 Task 6 拆分顺序）：
- :mod:`.gate_runner` Gate / CoVe / Judge 后置评审（Step 1）
- :mod:`.draft_persistence` _save_draft 落库 + 占位符出口闸门（Step 2）
- :mod:`.material_context` 素材上下文构建（Step 3）
- :mod:`.binding_loader` 绑定 / 章节 / 约束加载（Step 4）
- :mod:`.template_route` fixed_template 路由 + 表格重建（Step 5）
- :mod:`.data_fill_route` data_fill 路由（Step 6）
- :mod:`.free_writing_route` free_writing 路由 + token 预算 + 摘要（Step 7）

本 ``__init__.py`` 故意保持空 barrel：强制按子模块路径 import，
避免主类与子模块之间产生循环符号依赖。
"""

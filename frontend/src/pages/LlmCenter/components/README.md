# LlmCenter Components

本目录承载从 `pages/LlmCenter.tsx` 拆出的 11 个 React 组件（R2-13 拆分产物）。

## 组件清单（Step 3-6 内分阶段迁入）

- `NodeCard.tsx`：单节点 card
- `StageAccordion.tsx`：受控展开的阶段折叠面板（使用 lib/buildFlowLanes）
- `NodeDrawer.tsx`：节点详情侧抽屉（5 个 handle* + 5 个内嵌 tab）
- `TracesTab.tsx`：调用轨迹 tab
- `HistoryTab.tsx`：发布历史 tab
- `ParamTable.tsx`：参数定义表（input/output/runtime 三段）
- `PromptPreview.tsx`：prompt 文本预览
- `BatchResultPanel.tsx`：批量校验结果面板
- `ConfirmDialog.tsx`：通用确认对话框

## 拆分约束

- 每个组件文件 ≤ 350 行（R2-13 完成标准）
- 组件之间通过 prop 显式依赖，禁止直接互调内部 useState
- 跨组件共享的常量字典（CHANNEL_COLORS / STATUS_COLORS / FLOW_TABS）一律从 `../lib/constants` 引入
- 拓扑排序 helper 从 `../lib/buildFlowLanes` 引入
- 类型定义（ViewMode 等）从 `../types` 引入

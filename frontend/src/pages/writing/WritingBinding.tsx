/**
 * 写标·素材关联页 lazy 入口 shim。
 *
 * App.tsx `React.lazy(() => import('./pages/writing/WritingBinding'))` 通过本文件解析；
 * 实际编排器位于 `./WritingBinding/index.tsx`。
 *
 * 教训：本物理文件不可删除（路由 lazy 路径锚点）。
 * 历史 1113 行的实现已按 R2-11 拆为：
 * - WritingBinding/index.tsx (主编排器 ≤ 400 行)
 * - WritingBinding/hooks/* (5 个 hook)
 * - WritingBinding/components/* (5 个子组件)
 * - WritingBinding/{bindingDerivations,bindingLabel,constants,types}.ts (4 个平铺纯函数模块)
 */

export { default } from './WritingBinding/index'

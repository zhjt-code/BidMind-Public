/**
 * RequirementReview lazy 路由兼容 shim。
 *
 * 实际实现位于 `./RequirementReview/index.tsx`。
 *
 * 保留本物理文件的原因（沿用 v1 教训）:
 * - `pages/index.ts::RequirementReviewPage` 通过 `from './RequirementReview'` 引用
 * - `App.tsx` 的 lazy 路由通过 Vite 路径解析锚定此文件
 * - 删除会导致 lazy chunk 解析失败 / 路由白屏
 *
 * 本文件仅做 re-export，禁止在此处新增任何业务逻辑。
 */
export { default } from './RequirementReview/index'

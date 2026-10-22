/**
 * 写标素材页 re-export shim。
 *
 * 历史教训：vite dev server 缓存 React.lazy 引用的 dynamic
 * import 路径（带 timestamp）。若直接删除此文件，浏览器会持续 404 拉取
 * 旧的 `/src/pages/writing/WritingMaterials.tsx?t=...`。
 *
 * 因此目录化重构时必须保留此处作为 re-export shim，让 vite HMR 能
 * 平滑过渡到新目录结构。真实实现位于 `./WritingMaterials/index.tsx`。
 */
export { default } from './WritingMaterials/index'

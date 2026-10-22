/**
 * R2-13 shim: 保留 ./pages/LlmCenter 物理文件作为 React.lazy 锚点，
 * 防止 vite dev server 模块图谱缓存导致 dynamic import 404
 * （教训：重构 React.lazy 目标路径时，不要删除被 lazy 引用的物理文件）。
 *
 * 真实实现位于 ./LlmCenter/index.tsx；本文件仅 re-export default。
 */
export { default } from './LlmCenter/index'

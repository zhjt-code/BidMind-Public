/**
 * WritingMaterials 页面内部类型定义
 *
 * 仅服务于本目录，禁止外部依赖。
 */

/**
 * 子素材分组键：
 * - mixed: 图文混合（含图片的文本子素材）
 * - text: 纯文本子素材
 * - table: 表格子素材
 * - image: 纯图片子素材
 * - ignored: 已忽略的子素材
 */
export type ChildGroupKey = 'text' | 'mixed' | 'table' | 'image' | 'ignored'

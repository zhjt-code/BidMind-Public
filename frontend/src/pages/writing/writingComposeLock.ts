/**
 * 职责: 判定写标内容页"首次全文生成"期间是否锁定章节交互(纯函数)
 * 不做: 不发请求、不读全局 store、不承担生成进度轮询或章节树构建
 *
 * 锁定语义: 生成任务活跃且仍有叶子章节没有当前草稿即视为"首次生成进行中"。
 * 该判定只依赖服务端可重建的数据(目录节点 + 草稿归属)，页面重挂载后仍成立；
 * 重新生成全文时全部叶子已有旧草稿，不会触发锁定。
 */

export interface ComposeLockOutlineNode {
  id: number
  parentId: number | null
}

export interface ComposeLockInput {
  composing: boolean
  outlineNodes: ComposeLockOutlineNode[]
  draftedNodeIds: Iterable<number>
}

export function isFirstComposeInteractionLocked(input: ComposeLockInput): boolean {
  if (!input.composing) return false
  const { outlineNodes } = input
  // 目录尚未加载完成时无法证明生成已覆盖全部章节，保守锁定
  if (outlineNodes.length === 0) return true

  const parentIds = new Set<number>()
  for (const node of outlineNodes) {
    if (node.parentId != null) parentIds.add(node.parentId)
  }
  const leafIds = outlineNodes
    .filter((node) => !parentIds.has(node.id))
    .map((node) => node.id)
  if (leafIds.length === 0) return true

  const drafted = new Set(input.draftedNodeIds)
  return leafIds.some((id) => !drafted.has(id))
}

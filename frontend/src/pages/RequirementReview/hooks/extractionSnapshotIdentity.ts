/**
 * 职责: 解释招标要求提取状态快照的 run_id/revision，并判定可否消费。
 * 不做: 不执行轮询、不映射 UI 步骤、不写 React 状态、不做全局进度钳制。
 */

export interface ExtractionSnapshotIdentity {
  runId: string
  revision: number
}

export interface ExtractionStatusIdentityFields {
  run_id?: string | null
  revision?: number | null
}

export function readExtractionSnapshotIdentity(
  status: ExtractionStatusIdentityFields | null | undefined,
): ExtractionSnapshotIdentity | null {
  if (!status) return null
  const runId = typeof status.run_id === 'string' ? status.run_id.trim() : ''
  const revision = Number(status.revision)
  if (!runId || !Number.isFinite(revision)) return null
  return { runId, revision }
}

/**
 * 判定入站快照是否可被当前轮询会话消费。
 *
 * 规则:
 * - active 尚未建立时，允许首份缺字段快照（滚动发布兼容）
 * - active 已建立后，缺字段快照一律拒绝
 * - runId 必须相同；revision 必须严格大于已接受的最高 revision
 */
export function shouldAcceptExtractionSnapshot(
  active: ExtractionSnapshotIdentity | null,
  incoming: ExtractionSnapshotIdentity | null,
): boolean {
  if (active == null) {
    return true
  }
  if (incoming == null) {
    return false
  }
  if (incoming.runId !== active.runId) {
    return false
  }
  return incoming.revision > active.revision
}

/**
 * 在接受快照后推进 active 身份。
 * - 无 active：若入站有身份则采用，否则保持 null（兼容旧后端）
 * - 有 active：仅在同 run 且更高 revision 时更新 revision
 */
export function advanceExtractionSnapshotIdentity(
  active: ExtractionSnapshotIdentity | null,
  incoming: ExtractionSnapshotIdentity | null,
): ExtractionSnapshotIdentity | null {
  if (incoming == null) return active
  if (active == null) return incoming
  if (incoming.runId !== active.runId) return active
  if (incoming.revision <= active.revision) return active
  return incoming
}

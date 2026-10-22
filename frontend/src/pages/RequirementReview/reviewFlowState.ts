export type RequirementReviewPrimaryActionKind = 'confirm' | 'progress' | 'result'

export interface RequirementReviewPrimaryAction {
  kind: RequirementReviewPrimaryActionKind
  label: string
  targetPath: string | null
  title: string
}

/**
 * 从服务端项目状态派生招标要求确认页主按钮行为。
 *
 * 关键约束：审查一旦发起，确认页只能作为回看入口，不得再次暴露启动审查按钮。
 */
export function getRequirementReviewPrimaryAction(
  projectId: string | number,
  projectStatus: string | null | undefined,
): RequirementReviewPrimaryAction {
  const pid = String(projectId)

  if (projectStatus === 'reviewing' || projectStatus === 'failed') {
    return {
      kind: 'progress',
      label: '下一步',
      targetPath: `/project/${pid}/progress`,
      title: projectStatus === 'failed' ? '查看失败原因与恢复入口' : '查看审查进度',
    }
  }

  if (projectStatus === 'completed') {
    return {
      kind: 'result',
      label: '下一步',
      targetPath: `/project/${pid}/result`,
      title: '查看审查结果',
    }
  }

  return {
    kind: 'confirm',
    label: '确认并开始审查',
    targetPath: null,
    title: '',
  }
}

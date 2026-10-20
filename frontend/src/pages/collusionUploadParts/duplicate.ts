/**
 * 串标上传页重名检测纯函数（CollusionUpload 私有）。
 *
 * 抽自 CollusionUpload.tsx 的 checkDuplicateFiles useCallback；按 §1.2 决议
 * 不抽 hook（单点使用，hook 化是过度抽象），仅按"纯函数 + 子组件"拆分。
 *
 * 业务约束：
 *   - 串标场景多投标文件并发上传，文件名是唯一区分投标人的可见标识；
 *     重名导致结果不可读，因此上传前必须显式确认
 *   - 检测维度：与已有 existing bids 比对 + 新批次内自身比对
 *   - 仅 status !== 'error' 的 bidFiles 才占用文件名（失败重传不算冲突）
 */

export interface DuplicateCheckResult {
  /** 此次新选文件中与已有名集合或同批自身重复的文件名（去重） */
  duplicateNames: string[]
  /** 始终是入参 newFiles 自身（用户确认后是否继续添加由调用方决定） */
  filesToAdd: File[]
}

/**
 * 检测新选文件中是否存在与 existingNames 重名或自身重名。
 *
 * @param newFiles 用户当前选中（或拖入）的新文件列表
 * @param existingNames 当前页面已知文件名集合（已有 bid filename + status!=='error'
 *   的 pending/uploading/success bid 名）。调用方决定如何组装。
 */
export function checkDuplicateFiles(
  newFiles: File[],
  existingNames: string[],
): DuplicateCheckResult {
  const existingSet = new Set(existingNames)
  const duplicateNames: string[] = []
  const seenNames = new Set<string>()

  for (const f of newFiles) {
    if (existingSet.has(f.name) || seenNames.has(f.name)) {
      if (!duplicateNames.includes(f.name)) {
        duplicateNames.push(f.name)
      }
    }
    seenNames.add(f.name)
  }

  return { duplicateNames, filesToAdd: newFiles }
}

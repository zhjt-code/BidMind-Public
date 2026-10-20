export const PROJECT_LIST_PAGE_MEMORY_STORAGE_KEY = 'project-list-page-memory'

export type ProjectListPageTab = 'all' | 'bid_writing' | 'bid_review' | 'collusion_check'

export type ProjectListPageMemory = Record<ProjectListPageTab, number>

type ProjectListPageMemoryStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

interface PageMemoryOptions {
  storage?: ProjectListPageMemoryStorage | null
}

interface GetRememberedPageOptions extends PageMemoryOptions {
  totalPages?: number
}

const PROJECT_LIST_PAGE_TABS: ProjectListPageTab[] = [
  'all',
  'bid_writing',
  'bid_review',
  'collusion_check',
]

const DEFAULT_PROJECT_LIST_PAGE_MEMORY: ProjectListPageMemory = {
  all: 1,
  bid_writing: 1,
  bid_review: 1,
  collusion_check: 1,
}

function getDefaultStorage(): ProjectListPageMemoryStorage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

function resolveStorage(storage?: ProjectListPageMemoryStorage | null): ProjectListPageMemoryStorage | null {
  return storage === undefined ? getDefaultStorage() : storage
}

function normalizePage(page: unknown, totalPages?: number): number {
  const parsed = typeof page === 'number' ? page : Number(page)
  const safePage = Number.isFinite(parsed) ? Math.floor(parsed) : 1
  const lowerBounded = Math.max(1, safePage)
  if (typeof totalPages !== 'number') return lowerBounded
  return Math.min(lowerBounded, Math.max(1, Math.floor(totalPages)))
}

function normalizeMemory(value: unknown): ProjectListPageMemory {
  const memory = { ...DEFAULT_PROJECT_LIST_PAGE_MEMORY }
  if (!value || typeof value !== 'object') return memory

  const raw = value as Partial<Record<ProjectListPageTab, unknown>>
  for (const tab of PROJECT_LIST_PAGE_TABS) {
    memory[tab] = normalizePage(raw[tab])
  }

  return memory
}

export function readProjectListPageMemory(options: PageMemoryOptions = {}): ProjectListPageMemory {
  const storage = resolveStorage(options.storage)
  if (!storage) return { ...DEFAULT_PROJECT_LIST_PAGE_MEMORY }

  try {
    const raw = storage.getItem(PROJECT_LIST_PAGE_MEMORY_STORAGE_KEY)
    if (!raw) return { ...DEFAULT_PROJECT_LIST_PAGE_MEMORY }
    return normalizeMemory(JSON.parse(raw))
  } catch {
    return { ...DEFAULT_PROJECT_LIST_PAGE_MEMORY }
  }
}

function writeProjectListPageMemory(
  memory: ProjectListPageMemory,
  options: PageMemoryOptions = {},
): void {
  const storage = resolveStorage(options.storage)
  if (!storage) return

  try {
    storage.setItem(PROJECT_LIST_PAGE_MEMORY_STORAGE_KEY, JSON.stringify(memory))
  } catch {
    // sessionStorage 不可写时只降级为组件内状态，不阻断用户操作。
  }
}

export function getRememberedProjectListPage(
  tab: ProjectListPageTab,
  options: GetRememberedPageOptions = {},
): number {
  const memory = readProjectListPageMemory(options)
  return normalizePage(memory[tab], options.totalPages)
}

export function setRememberedProjectListPage(
  tab: ProjectListPageTab,
  page: number,
  options: PageMemoryOptions = {},
): ProjectListPageMemory {
  const memory = readProjectListPageMemory(options)
  memory[tab] = normalizePage(page)
  writeProjectListPageMemory(memory, options)
  return memory
}

export function resetProjectListPage(
  tab: ProjectListPageTab,
  options: PageMemoryOptions = {},
): ProjectListPageMemory {
  return setRememberedProjectListPage(tab, 1, options)
}

export function clearProjectListPageMemory(options: PageMemoryOptions = {}): void {
  const storage = resolveStorage(options.storage)
  if (!storage) return

  try {
    storage.removeItem(PROJECT_LIST_PAGE_MEMORY_STORAGE_KEY)
  } catch {
    // 清理失败不影响登出主流程。
  }
}

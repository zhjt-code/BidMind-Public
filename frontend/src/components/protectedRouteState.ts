export interface AuthReadySnapshot {
  isAuthenticated: boolean
  accessToken: string | null
  refreshToken: string | null
}

interface ReuseAuthReadyArgs {
  isAuthenticated: boolean
  accessToken: string | null
  cachedToken: string | null
  hasPromise: boolean
}

export function canReuseAuthReadyPromise({
  isAuthenticated,
  accessToken,
  cachedToken,
  hasPromise,
}: ReuseAuthReadyArgs): boolean {
  return Boolean(isAuthenticated && accessToken && hasPromise && cachedToken === accessToken)
}

export function shouldResetAuthReadyCache(
  prevState: AuthReadySnapshot,
  nextState: AuthReadySnapshot,
): boolean {
  return (
    (prevState.isAuthenticated && !nextState.isAuthenticated) ||
    prevState.accessToken !== nextState.accessToken ||
    prevState.refreshToken !== nextState.refreshToken
  )
}

interface AuthReadyRunState {
  runId: number
  activeRunId: number
  expectedToken: string
  currentToken: string | null
  isAuthenticated: boolean
}

export function isStaleAuthReadyRun({
  runId,
  activeRunId,
  expectedToken,
  currentToken,
  isAuthenticated,
}: AuthReadyRunState): boolean {
  return !isAuthenticated || runId !== activeRunId || currentToken !== expectedToken
}

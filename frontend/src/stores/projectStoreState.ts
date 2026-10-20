export function shouldResetProjectStateForAuthChange(
  prevUserId: number | null,
  nextUserId: number | null,
  isAuthenticated: boolean,
): boolean {
  return !isAuthenticated || prevUserId !== nextUserId
}

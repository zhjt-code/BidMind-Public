interface ProjectListViewArgs {
  loading: boolean
  hasLoadedOnce: boolean
  error: string | null
  projectsCount: number
  filteredProjectsCount: number
}

export type ProjectListViewState =
  | 'loading'
  | 'load_error'
  | 'empty_all'
  | 'empty_filtered'
  | 'list'

export function getProjectListViewState({
  loading,
  hasLoadedOnce,
  error,
  projectsCount,
  filteredProjectsCount,
}: ProjectListViewArgs): ProjectListViewState {
  if (loading || !hasLoadedOnce) {
    return 'loading'
  }

  if (error && projectsCount === 0) {
    return 'load_error'
  }

  if (projectsCount === 0) {
    return 'empty_all'
  }

  if (filteredProjectsCount === 0) {
    return 'empty_filtered'
  }

  return 'list'
}

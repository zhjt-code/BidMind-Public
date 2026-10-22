export type ViewMode = 'business' | 'technical'

export interface ConfirmDialogState {
  open: boolean
  title: string
  message: string
  confirmLabel: string
  onConfirm: () => void
}

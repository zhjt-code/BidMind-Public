import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import Header from './Header'
import BroadcastBanner from './BroadcastBanner'
import FeedbackPanel from './FeedbackPanel'
import HelpPanel from './HelpPanel'
import BackToTop from './BackToTop'
import { CreateProjectDialog, DuplicateConfirmDialog } from '@/pages/projectList/CreateProjectDialog'
import { useCreateProjectFlow } from '@/hooks/useCreateProjectFlow'
import { useMessageStore } from '@/stores'

const POLL_INTERVAL_MS = 30_000

const Layout: React.FC = () => {
  const [showFeedback, setShowFeedback] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const navigate = useNavigate()
  const mainRef = useRef<HTMLElement | null>(null)

  const { unreadCount, refreshUnread, fetchBanners } = useMessageStore()
  const toastShownRef = useRef(false)

  const {
    isOpen,
    createStep,
    selectedType,
    newProjectName,
    duplicateConfirm,
    handleOpen,
    handleClose,
    handleTypeSelect,
    setNewProjectName,
    handleCreate,
    handleConfirmDuplicate,
    handleCancelDuplicate,
    setCreateStep,
  } = useCreateProjectFlow()

  useEffect(() => {
    if (isOpen) handleOpen()
  }, [isOpen, handleOpen])

  const stablePoll = useCallback(() => {
    refreshUnread()
    fetchBanners()
  }, [refreshUnread, fetchBanners])

  useEffect(() => {
    stablePoll()
    const timer = setInterval(stablePoll, POLL_INTERVAL_MS)

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') stablePoll()
    }
    document.addEventListener('visibilitychange', handleVisibility)

    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [stablePoll])

  useEffect(() => {
    if (
      unreadCount > 0
      && !toastShownRef.current
      && sessionStorage.getItem('msg_toast_shown') !== '1'
    ) {
      toastShownRef.current = true
      sessionStorage.setItem('msg_toast_shown', '1')
      toast.info(`您有 ${unreadCount} 条未读消息`, {
        action: {
          label: '查看',
          onClick: () => navigate('/messages'),
        },
        duration: 5000,
      })
    }
  }, [unreadCount, navigate])

  return (
    <div className="fixed inset-0 bg-gray-100 flex flex-col overflow-hidden">
      <Header
        onOpenHelp={() => setShowHelp(true)}
        onOpenFeedback={() => setShowFeedback(true)}
      />

      <BroadcastBanner />

      <main ref={mainRef} className="flex flex-1 min-h-0 flex-col overflow-y-auto overflow-x-hidden max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet />
      </main>

      <BackToTop scrollContainerRef={mainRef} />

      <CreateProjectDialog
        open={isOpen}
        onOpenChange={(open) => { if (!open) handleClose() }}
        createStep={createStep}
        selectedType={selectedType}
        newProjectName={newProjectName}
        onTypeSelect={handleTypeSelect}
        onNameChange={setNewProjectName}
        onBack={() => setCreateStep('type')}
        onCreate={handleCreate}
      />
      <DuplicateConfirmDialog
        duplicateName={duplicateConfirm}
        onCancel={handleCancelDuplicate}
        onConfirm={handleConfirmDuplicate}
      />

      <FeedbackPanel
        visible={showFeedback || undefined}
        onClose={() => setShowFeedback(false)}
      />

      <HelpPanel
        open={showHelp}
        onOpenChange={setShowHelp}
        onOpenFeedback={() => setShowFeedback(true)}
      />
    </div>
  )
}

export default Layout

import React, { useRef, useState } from 'react'
import { Outlet } from 'react-router-dom'
import Header from './Header'
import BroadcastBanner from './BroadcastBanner'
import FeedbackPanel from './FeedbackPanel'
import HelpPanel from './HelpPanel'
import BackToTop from './BackToTop'
import AdminSideNav from './admin/AdminSideNav'

const AdminLayout: React.FC = () => {
  const [showFeedback, setShowFeedback] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [sidenavPinned, setSidenavPinned] = useState(false)
  const mainRef = useRef<HTMLElement | null>(null)

  return (
    <div className="fixed inset-0 bg-gray-50 flex flex-col overflow-hidden">
      <Header
        onOpenHelp={() => setShowHelp(true)}
        onOpenFeedback={() => setShowFeedback(true)}
      />

      <BroadcastBanner />

      <div className="flex-1 flex min-h-0">
        <AdminSideNav onPinnedChange={setSidenavPinned} />
        <main ref={mainRef} className="flex-1 min-h-0 overflow-auto overscroll-contain">
          <div
            className={`mx-auto px-6 py-8 ${
              sidenavPinned ? 'max-w-6xl' : 'max-w-7xl'
            }`}
          >
            <Outlet />
          </div>
        </main>
      </div>

      <BackToTop scrollContainerRef={mainRef} />

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

export default AdminLayout

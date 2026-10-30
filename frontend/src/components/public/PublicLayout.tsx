import React from 'react'
import { Outlet } from 'react-router-dom'
import PublicHeader from './PublicHeader'
import PublicFooter from './PublicFooter'

interface PublicLayoutProps {
  children?: React.ReactNode
}

const PublicLayout: React.FC<PublicLayoutProps> = ({ children }) => {
  return (
    <div className="public-shell min-h-screen antialiased">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-100 focus:rounded-md focus:bg-white focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-primary focus:ring-2 focus:ring-primary"
      >
        跳到主要内容
      </a>
      <PublicHeader />
      <main id="main-content" className="page-rail">
        {children ?? <Outlet />}
      </main>
      <PublicFooter />
    </div>
  )
}

export default PublicLayout

import React from 'react'
import { siteConfig } from '@/config/site'

const PublicFooter: React.FC = () => {
  return (
    <footer className="border-t border-white/10 bg-[#0A2540]">
      <p className="mx-auto max-w-7xl px-4 py-4 text-center text-xs text-white/40 sm:px-6 lg:px-8">
        &copy; 2026 BidMind AI
        {siteConfig.icpRecord ? (
          <>
            <span className="mx-1.5 text-white/25">&middot;</span>
            <a
              href="https://beian.miit.gov.cn/"
              target="_blank"
              rel="noopener noreferrer"
              className="transition-colors hover:text-white/70"
            >
              {siteConfig.icpRecord}
            </a>
          </>
        ) : null}
      </p>
    </footer>
  )
}

export default PublicFooter

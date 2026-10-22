import React from 'react'
import { Link } from 'react-router-dom'
import { Menu, X } from 'lucide-react'
import Logo from '@/components/Logo'
import { APP_HOME, LOGIN_PATH, PUBLIC_HOME, REGISTER_PATH } from '@/config/routes'
import { publicNavItems, type PublicNavHref } from '@/content/publicNav'
import { useAuthStore } from '@/stores'

function navLinkClassName(active: boolean): string {
  return active
    ? 'rounded-lg bg-primary px-2.5 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90 lg:px-3'
    : 'rounded-lg px-2.5 py-2 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950 lg:px-3'
}

function mobileNavLinkClassName(active: boolean): string {
  return active
    ? 'rounded-lg bg-primary px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-primary/90'
    : 'rounded-lg px-3 py-2.5 text-sm font-medium text-[#425466] transition-colors hover:bg-[#F6F9FC] hover:text-[#0A2540]'
}

const PublicHeader: React.FC = () => {
  const { isAuthenticated } = useAuthStore()
  const [mobileMenuOpen, setMobileMenuOpen] = React.useState(false)
  const [activeHref, setActiveHref] = React.useState<PublicNavHref | ''>('')
  const actionLabel = isAuthenticated ? '进入工作台' : '立即注册'
  const actionPath = isAuthenticated ? APP_HOME : REGISTER_PATH

  React.useEffect(() => {
    const sectionIds = publicNavItems.map(item => item.href.slice(1))
    const visibility = new Map<string, number>()

    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          visibility.set(entry.target.id, entry.isIntersecting ? entry.intersectionRatio : 0)
        }
        let bestId = ''
        let bestRatio = 0
        for (const id of sectionIds) {
          const ratio = visibility.get(id) ?? 0
          if (ratio > bestRatio) {
            bestRatio = ratio
            bestId = id
          }
        }
        if (bestId) setActiveHref(`#${bestId}` as PublicNavHref)
      },
      { rootMargin: '-18% 0px -62% 0px', threshold: [0, 0.15, 0.35, 0.55, 0.75, 1] },
    )

    for (const id of sectionIds) {
      const element = document.getElementById(id)
      if (element) observer.observe(element)
    }

    return () => observer.disconnect()
  }, [])

  const handleNavClick = (event: React.MouseEvent<HTMLAnchorElement>, href: PublicNavHref) => {
    event.preventDefault()
    setActiveHref(href)
    setMobileMenuOpen(false)
    const target = document.getElementById(href.slice(1))
    if (target) {
      target.scrollIntoView({ behavior: 'auto', block: 'start' })
    }
    if (window.location.hash !== href) {
      window.history.replaceState(null, '', href)
    }
  }

  return (
    <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/90 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center px-4 sm:px-6 lg:px-8">
        <Link to={PUBLIC_HOME} className="flex flex-shrink-0 items-center" aria-label="BidMind AI 首页">
          <Logo size="md" />
        </Link>

        <div
          className="mx-4 hidden h-6 w-px flex-shrink-0 bg-slate-200 md:block lg:mx-6"
          aria-hidden="true"
        />

        <nav className="hidden items-center gap-1 md:flex" aria-label="公开首页导航">
          {publicNavItems.map(item => (
            <a
              key={item.href}
              href={item.href}
              aria-current={activeHref === item.href ? 'true' : undefined}
              className={navLinkClassName(activeHref === item.href)}
              onClick={event => handleNavClick(event, item.href)}
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-[#425466] transition-colors hover:bg-slate-100 md:hidden"
            aria-label={mobileMenuOpen ? '关闭导航菜单' : '打开导航菜单'}
            aria-expanded={mobileMenuOpen}
            aria-controls="public-mobile-navigation"
            onClick={() => setMobileMenuOpen(open => !open)}
          >
            {mobileMenuOpen ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
          </button>
          {!isAuthenticated && (
            <Link
              to={LOGIN_PATH}
              className="hidden rounded-lg px-3 py-2 text-sm font-semibold text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-950 sm:inline-flex"
            >
              登录
            </Link>
          )}
          <Link
            to={actionPath}
            className="inline-flex h-10 items-center rounded-xl bg-primary px-4 text-sm font-semibold text-white transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          >
            {actionLabel}
          </Link>
        </div>
      </div>
      {mobileMenuOpen && (
        <nav
          id="public-mobile-navigation"
          className="border-t border-slate-200 bg-white px-4 py-3 md:hidden"
          aria-label="移动端公开首页导航"
        >
          <div className="mx-auto grid max-w-7xl gap-1">
            {publicNavItems.map(item => (
              <a
                key={item.href}
                href={item.href}
                aria-current={activeHref === item.href ? 'true' : undefined}
                className={mobileNavLinkClassName(activeHref === item.href)}
                onClick={event => handleNavClick(event, item.href)}
              >
                {item.label}
              </a>
            ))}
          </div>
        </nav>
      )}
    </header>
  )
}

export default PublicHeader

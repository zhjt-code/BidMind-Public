import React from 'react'

/** 品牌图标：横向 PNG，按高度等比缩放（源图约 1.44:1） */
const BRAND_LOGO_SRC = '/brand-logo.png'
/** 品牌字标：横向 PNG，高度对齐原 text-* 字号，宽度随比例扩展（源图约 4.33:1） */
const BRAND_WORDMARK_SRC = '/brand-wordmark.png'
const WORDMARK_ASPECT = 4.333

interface LogoIconProps {
  size?: number
  className?: string
}

export const LogoIcon: React.FC<LogoIconProps> = ({ size = 32, className = '' }) => (
  <img
    src={BRAND_LOGO_SRC}
    alt=""
    width={Math.round(size * 1.442)}
    height={size}
    className={`flex-shrink-0 object-contain ${className}`.trim()}
    style={{ height: size, width: 'auto' }}
    decoding="async"
  />
)

interface LogoWordmarkProps {
  height: number
  className?: string
}

const LogoWordmark: React.FC<LogoWordmarkProps> = ({ height, className = '' }) => (
  <img
    src={BRAND_WORDMARK_SRC}
    alt="岚墨 AI"
    width={Math.round(height * WORDMARK_ASPECT)}
    height={height}
    className={`flex-shrink-0 object-contain ${className}`.trim()}
    style={{ height, width: 'auto' }}
    decoding="async"
  />
)

interface LogoProps {
  size?: 'sm' | 'md' | 'lg' | 'xl'
  showText?: boolean
  className?: string
}

/** textHeight：新字标几乎无上下留白。md 为页头档（旧 44 的 60% 后再收 10%）；xl 给登录页，不跟页头连动 */
const sizeMap = {
  sm: { icon: 28, textHeight: 17, gap: 'gap-2' },
  md: { icon: 40, textHeight: 23, gap: 'gap-1.5' },
  lg: { icon: 40, textHeight: 26, gap: 'gap-3' },
  xl: { icon: 52, textHeight: 34, gap: 'gap-4' },
}

const Logo: React.FC<LogoProps> = ({ size = 'md', showText = true, className = '' }) => {
  const { icon, textHeight, gap } = sizeMap[size]

  return (
    <div className={`flex items-center ${gap} ${className}`}>
      <LogoIcon size={icon} />
      {showText && <LogoWordmark height={textHeight} />}
    </div>
  )
}

export default Logo

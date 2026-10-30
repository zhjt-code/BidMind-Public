import React, { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'

interface GuideStep {
  target: string
  title: string
  description: string
  placement: 'top' | 'bottom' | 'left' | 'right'
}

interface GuideTourProps {
  steps: GuideStep[]
  storageKey: string
  delay?: number
}

const PADDING = 6
const ARROW_SIZE = 8
const POLL_INTERVAL = 500
const POLL_TIMEOUT = 10_000

const GuideTour: React.FC<GuideTourProps> = ({ steps, storageKey, delay = 800 }) => {
  const [currentStep, setCurrentStep] = useState(-1)
  const [targetRect, setTargetRect] = useState<DOMRect | null>(null)
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollStart = useRef(0)

  const alreadySeen = useRef(() => {
    try {
      return localStorage.getItem(storageKey) === 'true'
    } catch {
      return false
    }
  })

  const markSeen = useCallback(() => {
    try {
      localStorage.setItem(storageKey, 'true')
    } catch { /* noop */ }
  }, [storageKey])

  useEffect(() => {
    if (alreadySeen.current()) return
    const timer = setTimeout(() => setCurrentStep(0), delay)
    return () => clearTimeout(timer)
  }, [delay])

  const clearPoll = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current)
      pollTimer.current = null
    }
  }, [])

  const updateRect = useCallback((selector: string): boolean => {
    const el = document.querySelector(selector)
    if (!el) return false
    setTargetRect(el.getBoundingClientRect())
    return true
  }, [])

  useEffect(() => {
    if (currentStep < 0 || currentStep >= steps.length) return
    clearPoll()

    const step = steps[currentStep]
    if (updateRect(step.target)) return

    pollStart.current = Date.now()
    pollTimer.current = setInterval(() => {
      if (updateRect(step.target)) {
        clearPoll()
        return
      }
      if (Date.now() - pollStart.current > POLL_TIMEOUT) {
        clearPoll()
        if (currentStep < steps.length - 1) {
          setCurrentStep(prev => prev + 1)
        } else {
          markSeen()
          setCurrentStep(-1)
        }
      }
    }, POLL_INTERVAL)

    return clearPoll
  }, [currentStep, steps, updateRect, clearPoll, markSeen])

  useEffect(() => {
    if (currentStep < 0 || currentStep >= steps.length) return

    const step = steps[currentStep]
    const onLayout = () => updateRect(step.target)
    window.addEventListener('resize', onLayout)
    window.addEventListener('scroll', onLayout, true)
    return () => {
      window.removeEventListener('resize', onLayout)
      window.removeEventListener('scroll', onLayout, true)
    }
  }, [currentStep, steps, updateRect])

  const handleNext = useCallback(() => {
    if (currentStep < steps.length - 1) {
      setTargetRect(null)
      setCurrentStep(prev => prev + 1)
    } else {
      markSeen()
      setCurrentStep(-1)
    }
  }, [currentStep, steps.length, markSeen])

  const handleSkip = useCallback(() => {
    markSeen()
    setCurrentStep(-1)
  }, [markSeen])

  if (currentStep < 0 || currentStep >= steps.length || !targetRect) return null

  const step = steps[currentStep]
  const isLast = currentStep === steps.length - 1

  const highlight = {
    top: targetRect.top - PADDING,
    left: targetRect.left - PADDING,
    width: targetRect.width + PADDING * 2,
    height: targetRect.height + PADDING * 2,
  }

  const popover = computePopoverPosition(targetRect, step.placement)

  return createPortal(
    <div className="fixed inset-0" style={{ zIndex: 9999 }}>
      {/* overlay with cutout */}
      <div
        className="absolute inset-0"
        style={{
          background: 'rgba(0, 0, 0, 0.45)',
          maskImage: `
            linear-gradient(#000 0 0),
            linear-gradient(#000 0 0)
          `,
          maskComposite: 'exclude',
          WebkitMaskImage: `
            linear-gradient(#000 0 0),
            linear-gradient(#000 0 0)
          `,
          WebkitMaskComposite: 'xor',
          maskPosition: `0 0, ${highlight.left}px ${highlight.top}px`,
          maskSize: `100% 100%, ${highlight.width}px ${highlight.height}px`,
          maskRepeat: 'no-repeat',
          WebkitMaskPosition: `0 0, ${highlight.left}px ${highlight.top}px`,
          WebkitMaskSize: `100% 100%, ${highlight.width}px ${highlight.height}px`,
          WebkitMaskRepeat: 'no-repeat',
        }}
      />

      {/* highlight border */}
      <div
        className="absolute rounded-lg border-2 border-primary/60 pointer-events-none"
        style={{
          top: highlight.top,
          left: highlight.left,
          width: highlight.width,
          height: highlight.height,
          boxShadow: '0 0 0 2px rgba(59, 130, 246, 0.15)',
        }}
      />

      {/* popover */}
      <div
        className="absolute bg-white rounded-xl shadow-2xl border border-gray-200 p-5 w-72 animate-in fade-in slide-in-from-bottom-2 duration-200"
        style={{ top: popover.top, left: popover.left }}
      >
        {/* arrow */}
        <div
          className="absolute w-0 h-0"
          style={getArrowStyle(step.placement, popover, targetRect)}
        />

        <div className="flex items-start justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-900">{step.title}</h3>
          <span className="text-xs text-gray-400 ml-2 shrink-0">
            {currentStep + 1}/{steps.length}
          </span>
        </div>
        <p className="text-sm text-gray-600 leading-relaxed mb-4">{step.description}</p>
        <div className="flex items-center justify-between">
          <button
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
            onClick={handleSkip}
          >
            跳过引导
          </button>
          <button
            className="px-4 py-1.5 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 transition-colors"
            onClick={handleNext}
          >
            {isLast ? '知道了' : '下一步'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function computePopoverPosition(
  rect: DOMRect,
  placement: GuideStep['placement'],
): { top: number; left: number } {
  const popoverWidth = 288
  const popoverEstHeight = 160
  const gap = PADDING + ARROW_SIZE + 4

  switch (placement) {
    case 'bottom':
      return {
        top: rect.bottom + gap,
        left: Math.max(8, rect.left + rect.width / 2 - popoverWidth / 2),
      }
    case 'top':
      return {
        top: rect.top - gap - popoverEstHeight,
        left: Math.max(8, rect.left + rect.width / 2 - popoverWidth / 2),
      }
    case 'left':
      return {
        top: rect.top + rect.height / 2 - popoverEstHeight / 2,
        left: rect.left - gap - popoverWidth,
      }
    case 'right':
      return {
        top: rect.top + rect.height / 2 - popoverEstHeight / 2,
        left: rect.right + gap,
      }
  }
}

function getArrowStyle(
  placement: GuideStep['placement'],
  popover: { top: number; left: number },
  rect: DOMRect,
): React.CSSProperties {
  const size = ARROW_SIZE
  const base: React.CSSProperties = {
    position: 'absolute',
    borderStyle: 'solid',
  }

  switch (placement) {
    case 'bottom':
      return {
        ...base,
        top: -size,
        left: Math.max(16, rect.left + rect.width / 2 - popover.left - size),
        borderWidth: `0 ${size}px ${size}px ${size}px`,
        borderColor: 'transparent transparent white transparent',
      }
    case 'top':
      return {
        ...base,
        bottom: -size,
        left: Math.max(16, rect.left + rect.width / 2 - popover.left - size),
        borderWidth: `${size}px ${size}px 0 ${size}px`,
        borderColor: 'white transparent transparent transparent',
      }
    case 'left':
      return {
        ...base,
        right: -size,
        top: Math.max(16, rect.top + rect.height / 2 - popover.top - size),
        borderWidth: `${size}px 0 ${size}px ${size}px`,
        borderColor: 'transparent transparent transparent white',
      }
    case 'right':
      return {
        ...base,
        left: -size,
        top: Math.max(16, rect.top + rect.height / 2 - popover.top - size),
        borderWidth: `${size}px ${size}px ${size}px 0`,
        borderColor: 'transparent white transparent transparent',
      }
  }
}

export default GuideTour

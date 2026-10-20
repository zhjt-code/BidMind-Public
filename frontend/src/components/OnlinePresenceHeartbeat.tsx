import React, { useEffect } from 'react'
import { presenceService } from '@/services/presenceApi'
import { useAuthStore } from '@/stores'

const HEARTBEAT_INTERVAL_MS = 30_000

const OnlinePresenceHeartbeat: React.FC = () => {
  const { isAuthenticated, accessToken } = useAuthStore()
  const heartbeatEnabled = isAuthenticated && !!accessToken

  useEffect(() => {
    if (!heartbeatEnabled) return undefined

    let stopped = false
    let timer: number | null = null

    const sendHeartbeat = async () => {
      if (stopped || document.visibilityState !== 'visible') return
      try {
        await presenceService.sendHeartbeat()
      } catch (error) {
        console.debug('Online presence heartbeat skipped:', error)
      }
    }

    const stopTimer = () => {
      if (timer !== null) {
        window.clearInterval(timer)
        timer = null
      }
    }

    const startTimer = () => {
      if (timer === null) {
        timer = window.setInterval(() => {
          void sendHeartbeat()
        }, HEARTBEAT_INTERVAL_MS)
      }
    }

    const syncVisibility = () => {
      if (document.visibilityState === 'visible') {
        void sendHeartbeat()
        startTimer()
      } else {
        stopTimer()
      }
    }

    syncVisibility()
    document.addEventListener('visibilitychange', syncVisibility)

    return () => {
      stopped = true
      stopTimer()
      document.removeEventListener('visibilitychange', syncVisibility)
    }
  }, [heartbeatEnabled])

  return null
}

export default OnlinePresenceHeartbeat

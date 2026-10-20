import { api } from './httpClient'
import type { ApiResponse } from './apiTypes'

interface PresenceHeartbeatResponse {
  ok: boolean
  degraded: boolean
  online_window_seconds: number
}

const transformHeartbeat = (data: PresenceHeartbeatResponse) => ({
  ok: data.ok,
  degraded: data.degraded,
  onlineWindowSeconds: data.online_window_seconds,
})

export const presenceService = {
  sendHeartbeat: async () => {
    const response = await api.post<ApiResponse<PresenceHeartbeatResponse>>('/presence/heartbeat')
    return transformHeartbeat(response.data)
  },
}

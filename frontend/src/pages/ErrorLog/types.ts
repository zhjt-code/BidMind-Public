export interface ErrorLogItem {
  id: number
  category: string
  level: string
  message: string
  traceback?: string | null
  module: string | null
  function_name: string | null
  line_number: number | null
  request_id: string | null
  user_id: number | null
  username: string | null
  project_id: number | null
  project_name: string | null
  task_type: string | null
  stage: string | null
  source: string
  resolved: boolean
  resolved_by: string | null
  resolved_at: string | null
  created_at: string | null
  incident_id: string | null
  chain_seq: number | null
  is_root_cause: boolean
  exception_type: string | null
  origin_module: string | null
  origin_function: string | null
  origin_line: number | null
}

export interface ErrorStats {
  total: number
  unresolved: number
  by_level: Record<string, number>
  by_source: Record<string, number>
  by_category: Record<string, number>
  by_hour: Record<string, number>
  top_modules: { module: string; count: number }[]
  hours: number
}

export interface SuppressionRule {
  id: number
  module: string
  function_name: string | null
  message_prefix: string
  level: string | null
  category: string | null
  enabled: boolean
  reason: string | null
  created_by: string
  source_log_id: number | null
  suppressed_count: number
  last_suppressed_at: string | null
  created_at: string | null
}

export interface ErrorLogGroup {
  fingerprint: string
  module: string | null
  level: string
  category: string | null
  source: string | null
  sample_message: string
  count: number
  unresolved_count: number
  latest_at: string | null
  earliest_at: string | null
  user_count: number
  project_count: number
}

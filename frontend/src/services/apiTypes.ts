// 后端统一响应包装格式
export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

// 分页响应统一结构
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

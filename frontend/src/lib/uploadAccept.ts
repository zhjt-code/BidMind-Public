export const ALLOWED_UPLOAD_SUFFIXES = ['.pdf', '.doc', '.docx'] as const

export const UPLOAD_ACCEPT = ALLOWED_UPLOAD_SUFFIXES.join(',')

export const OFFICE_CONVERT_DURATION_HINT = 'Word 大文件转换最长约 3 分钟'

export const UPLOAD_FORMAT_HINT = '支持 PDF、Word(.doc/.docx) 格式'

export const UPLOAD_FORMAT_TOAST = '仅支持 PDF 或 Word(.doc/.docx) 格式文件'

export function isAllowedUploadFile(name: string): boolean {
  const lower = name.toLowerCase()
  return ALLOWED_UPLOAD_SUFFIXES.some((ext) => lower.endsWith(ext))
}

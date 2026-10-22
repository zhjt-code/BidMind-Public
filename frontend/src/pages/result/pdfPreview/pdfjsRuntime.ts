/**
 * 职责: 提供 pdf.js worker 与 Document 运行时资源地址
 * 不做: 不渲染页面，不把 worker 指到哈希 .mjs
 */

export const PDFJS_WORKER_SRC = `${import.meta.env.BASE_URL}pdfjs/pdf.worker.min.js`

export const PDFJS_DOCUMENT_OPTIONS = {
  cMapUrl: `${import.meta.env.BASE_URL}cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `${import.meta.env.BASE_URL}standard_fonts/`,
  wasmUrl: `${import.meta.env.BASE_URL}wasm/`,
}

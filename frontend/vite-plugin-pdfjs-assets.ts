import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Plugin } from 'vite'

/**
 * 职责: 开发态托管、构建态拷贝 pdf.js worker 与 cmap/wasm/字体
 * 不做: 不改预览 UI，不引入 CDN
 */
const require = createRequire(import.meta.url)

const PDFJS_ASSET_DIRS = ['cmaps', 'wasm', 'standard_fonts'] as const
const WORKER_PUBLIC_PATH = '/pdfjs/pdf.worker.min.js'

function pdfjsPackageRoot(): string {
  return path.dirname(require.resolve('pdfjs-dist/package.json'))
}

function contentTypeFor(filePath: string): string {
  if (filePath.endsWith('.js') || filePath.endsWith('.mjs')) {
    return 'application/javascript; charset=utf-8'
  }
  if (filePath.endsWith('.wasm')) return 'application/wasm'
  return 'application/octet-stream'
}

function safeResolve(root: string, rel: string): string | null {
  if (!rel || rel.includes('\0')) return null
  const resolved = path.resolve(root, rel)
  const rootResolved = path.resolve(root)
  if (resolved !== rootResolved && !resolved.startsWith(rootResolved + path.sep)) {
    return null
  }
  return resolved
}

export function copyPdfjsAssetsTo(destRoot: string): void {
  const srcRoot = pdfjsPackageRoot()
  for (const dir of PDFJS_ASSET_DIRS) {
    fs.cpSync(path.join(srcRoot, dir), path.join(destRoot, dir), { recursive: true })
  }
  const workerDestDir = path.join(destRoot, 'pdfjs')
  fs.mkdirSync(workerDestDir, { recursive: true })
  fs.copyFileSync(
    path.join(srcRoot, 'build', 'pdf.worker.min.mjs'),
    path.join(workerDestDir, 'pdf.worker.min.js'),
  )
}

function sendFile(res: ServerResponse, filePath: string): void {
  res.setHeader('Content-Type', contentTypeFor(filePath))
  fs.createReadStream(filePath).pipe(res)
}

function servePdfjsDevAsset(req: IncomingMessage, res: ServerResponse): boolean {
  const url = req.url?.split('?')[0] ?? ''
  const srcRoot = pdfjsPackageRoot()

  if (url === WORKER_PUBLIC_PATH) {
    sendFile(res, path.join(srcRoot, 'build', 'pdf.worker.min.mjs'))
    return true
  }

  for (const dir of PDFJS_ASSET_DIRS) {
    const prefix = `/${dir}/`
    if (!url.startsWith(prefix)) continue
    const filePath = safeResolve(path.join(srcRoot, dir), decodeURIComponent(url.slice(prefix.length)))
    if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      return false
    }
    sendFile(res, filePath)
    return true
  }
  return false
}

export function pdfjsStaticAssets(): Plugin {
  return {
    name: 'pdfjs-static-assets',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (servePdfjsDevAsset(req, res)) return
        next()
      })
    },
    writeBundle(options) {
      if (!options.dir) return
      copyPdfjsAssetsTo(options.dir)
    },
  }
}

export const PDFJS_COPIED_DIRS = PDFJS_ASSET_DIRS
export { WORKER_PUBLIC_PATH }

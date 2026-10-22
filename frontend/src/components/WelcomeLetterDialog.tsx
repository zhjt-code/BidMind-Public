/**
 * 职责: 展示全文手写体欢迎信弹窗，并在用户确认后通过 API 写入确认状态。
 * 不做: 不负责路由守卫、鉴权逻辑或通用公告管理。
 * @requires 必须仅在 ProtectedRoute 的 authChecked 门禁内侧渲染。
 */

import React, { useState } from 'react'
import { useAuthStore } from '@/stores/authStore'
import { authService } from '@/services/authApi'
import { shouldShowWelcomeLetter } from './welcomeLetterState'
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'

const HANDWRITING_FONT: React.CSSProperties = {
  fontFamily: '"STKaiti", "KaiTi", "Kaiti SC", "FZKai-Z03", cursive',
}

const PAPER_STYLE: React.CSSProperties = {
  backgroundImage: `
    linear-gradient(135deg, #fefdf8 0%, #fdf9ee 50%, #fbf6e8 100%),
    repeating-linear-gradient(
      0deg,
      transparent,
      transparent 27px,
      rgba(180, 170, 140, 0.08) 27px,
      rgba(180, 170, 140, 0.08) 28px
    )
  `,
}

const INK_COLOR = '#1a3a5c'

/** 楷体无粗体字重时，用描边+阴影合成可读强调 */
const EMPHASIS_STYLE: React.CSSProperties = {
  fontWeight: 700,
  WebkitTextStroke: '0.55px currentColor',
  textShadow:
    '0.45px 0 0 currentColor, -0.45px 0 0 currentColor, 0 0.45px 0 currentColor, 0 -0.45px 0 currentColor',
}

const WelcomeLetterDialog: React.FC = () => {
  const { user, updateUser } = useAuthStore()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isOpen = shouldShowWelcomeLetter(user)

  const handleConfirm = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const updatedUser = await authService.acknowledgeWelcomeLetter()
      updateUser(updatedUser)
    } catch {
      setError('确认失败，请稍后重试。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={isOpen}>
      <DialogContent
        showCloseButton={false}
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
        className="w-full max-w-xl sm:max-w-2xl p-0 overflow-hidden border-0 shadow-2xl"
        style={PAPER_STYLE}
        aria-describedby="welcome-letter-description"
      >
        <div className="px-8 pt-8 pb-2 sm:px-10 sm:pt-10">
          <DialogTitle
            className="text-center text-xl sm:text-2xl font-normal leading-relaxed tracking-widest mb-6"
            style={{ ...HANDWRITING_FONT, color: INK_COLOR }}
          >
            致敬奋斗者
          </DialogTitle>

          <DialogDescription id="welcome-letter-description" className="sr-only">
            来自 BIDMIND AI 团队的欢迎信，请阅读后点击确认按钮。
          </DialogDescription>

          <div
            className="max-h-[52vh] overflow-y-auto pr-1 text-base sm:text-[1.05rem] leading-8 tracking-wide"
            style={{ ...HANDWRITING_FONT, color: INK_COLOR }}
          >
            <p className="mb-4">亲爱的奋斗者：</p>

            <p className="mb-4">您好！</p>

            <p className="mb-4">
              BIDMIND AI 历经近两年打磨，以近40万行代码和一次次严谨验证，为每一份结果提供扎实保障。
            </p>

            <p className="mb-4">
              标书编写、投标审查与串标审查都需深入理解大量文档，并经历多轮解析、推理与算力调度。因此，
              <strong style={EMPHASIS_STYLE}>
                部分任务可能需要较长时间
              </strong>
              ——这并非系统停滞，而是它正在认真处理每一个细节。
            </p>

            <p className="mb-4">
              我们理解等待并不轻松。请多给 BIDMIND AI 一点时间。严谨值得等待，您的耐心终将换来更完整、更可靠的结果。
            </p>

            <p className="mb-4">
              趁此片刻，不妨看看窗外，品一口茶，或起身舒展一下。愿这段等待，也能成为繁忙中难得的从容。
            </p>

            <p className="mb-4">
              感谢您的信任。期待与您一路同行，也欢迎随时提出宝贵建议。
            </p>

            <p className="text-right mt-8 mb-2">BIDMIND AI 团队敬上</p>
          </div>
        </div>

        <div className="px-8 pb-8 pt-4 sm:px-10 sm:pb-10 flex flex-col items-center gap-3">
          {error && (
            <p
              role="alert"
              className="text-sm text-red-600 text-center"
            >
              {error}
            </p>
          )}
          <button
            type="button"
            disabled={submitting}
            aria-busy={submitting}
            onClick={handleConfirm}
            className="w-full sm:w-auto min-w-[180px] px-8 py-3 rounded-md text-sm font-medium transition-colors
              bg-blue-800 text-white hover:bg-blue-900 focus:outline-none focus:ring-2 focus:ring-blue-700 focus:ring-offset-2
              disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {submitting ? '正在确认……' : '我已知晓，开始使用'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default WelcomeLetterDialog

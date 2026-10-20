import React, { Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import PublicLayout from './components/public/PublicLayout'
import ProtectedRoute from './components/ProtectedRoute'
import { TooltipProvider } from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores'
import { isLlmCenterEnabled } from '@/config/features'
import { APP_HOME, PUBLIC_HOME } from '@/config/routes'

const ProjectListPage = React.lazy(() => import('./pages/ProjectList'))
const FileUploadPage = React.lazy(() => import('./pages/FileUpload'))
const RequirementReviewPage = React.lazy(() => import('./pages/RequirementReview'))
const ProgressPage = React.lazy(() => import('./pages/Progress'))
const ResultPage = React.lazy(() => import('./pages/Result'))
const SettingsPage = React.lazy(() => import('./pages/Settings'))
const LoginPage = React.lazy(() => import('./pages/Login'))
const RegisterPage = React.lazy(() => import('./pages/Register'))
const VerifyEmailPage = React.lazy(() => import('./pages/VerifyEmail'))
const ForgotPasswordPage = React.lazy(() => import('./pages/ForgotPassword'))
const ResetPasswordPage = React.lazy(() => import('./pages/ResetPassword'))
const UserManagementPage = React.lazy(() => import('./pages/UserManagement'))
const FeedbackManagementPage = React.lazy(() => import('./pages/FeedbackManagement'))
const AuditLogPage = React.lazy(() => import('./pages/AuditLog'))
const ErrorLogPage = React.lazy(() => import('./pages/ErrorLog'))
const CollusionUploadPage = React.lazy(() => import('./pages/CollusionUpload'))
const CollusionProgressPage = React.lazy(() => import('./pages/CollusionProgress'))
const CollusionResultPage = React.lazy(() => import('./pages/CollusionResult'))
const KnowledgeBasePage = React.lazy(() => import('./pages/KnowledgeBase'))
const WritingUploadPage = React.lazy(() => import('./pages/writing/WritingUpload'))
const WritingProgressPage = React.lazy(() => import('./pages/writing/WritingProgress'))
const WritingClausesPage = React.lazy(() => import('./pages/writing/WritingClauses'))
const WritingOutlinePage = React.lazy(() => import('./pages/writing/WritingOutline'))
const WritingMaterialsPage = React.lazy(() => import('./pages/writing/WritingMaterials'))
const WritingBindingPage = React.lazy(() => import('./pages/writing/WritingBinding'))
const WritingComposePage = React.lazy(() => import('./pages/writing/WritingCompose'))
const WritingPreviewPage = React.lazy(() => import('./pages/writing/WritingPreview'))
const LlmCenterPage = React.lazy(() => import('./pages/LlmCenter'))
const MessagesPage = React.lazy(() => import('./pages/Messages'))
const OpsOverviewPage = React.lazy(() => import('./pages/admin/ops/Overview'))
const OpsTenderPage = React.lazy(() => import('./pages/admin/ops/Tender'))
const OpsCollusionPage = React.lazy(() => import('./pages/admin/ops/Collusion'))
const OpsWriterPage = React.lazy(() => import('./pages/admin/ops/Writer'))
const AdminBroadcastsPage = React.lazy(() => import('./pages/admin/Broadcasts'))
const ProjectTimelinePage = React.lazy(() => import('./pages/admin/ProjectTimeline'))
const Layout = React.lazy(() => import('./components/Layout'))
const AdminLayout = React.lazy(() => import('./components/AdminLayout'))
const ProjectFlowLayout = React.lazy(() => import('./components/ProjectFlowLayout'))
const HomePage = React.lazy(() => import('./pages/public/HomePage'))
const NotFoundPage = React.lazy(() => import('./pages/public/NotFoundPage'))

const PublicRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated } = useAuthStore()
  if (isAuthenticated) return <Navigate to={APP_HOME} replace />
  return <>{children}</>
}

const LazyFallback = () => (
  <div className="fixed inset-0 flex items-center justify-center">
    <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
  </div>
)

function App() {
  return (
    <TooltipProvider delayDuration={300}>
    <Suspense fallback={<LazyFallback />}>
    <Routes>
      {/* 企业公开首页 */}
      <Route path={PUBLIC_HOME} element={<PublicLayout />}>
        <Route index element={<HomePage />} />
      </Route>

      {/* 公开路由 */}
      <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
      <Route path="/register" element={<PublicRoute><RegisterPage /></PublicRoute>} />
      {/* 验证页对所有人开放(含已登录), 不重定向 */}
      <Route path="/verify-email" element={<VerifyEmailPage />} />
      <Route path="/forgot-password" element={<PublicRoute><ForgotPasswordPage /></PublicRoute>} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />

      {/* 受保护路由 + Layout */}
      <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
        <Route path="app" element={<ProjectListPage />} />
        <Route path="project/:id" element={<ProjectFlowLayout />}>
          <Route index element={<Navigate to="upload" replace />} />
          <Route path="upload" element={<FileUploadPage />} />
          <Route path="requirements" element={<RequirementReviewPage />} />
          <Route path="progress" element={<ProgressPage />} />
          <Route path="result" element={<ResultPage />} />
          <Route path="collusion/upload" element={<CollusionUploadPage />} />
          <Route path="collusion/progress" element={<CollusionProgressPage />} />
          <Route path="collusion/result" element={<CollusionResultPage />} />
          <Route path="writing/upload" element={<WritingUploadPage />} />
          <Route path="writing/progress" element={<WritingProgressPage />} />
          <Route path="writing/clauses" element={<WritingClausesPage />} />
          <Route path="writing/outline" element={<WritingOutlinePage />} />
          <Route path="writing/materials" element={<WritingMaterialsPage />} />
          <Route path="writing/binding" element={<WritingBindingPage />} />
          <Route path="writing/compose" element={<WritingComposePage />} />
          <Route path="writing/preview" element={<WritingPreviewPage />} />
        </Route>
        <Route path="knowledge-base" element={<KnowledgeBasePage />} />
        <Route path="messages" element={<MessagesPage />} />
      </Route>

      {/* 管理员路由 + AdminLayout */}
      <Route element={<ProtectedRoute requireAdmin><AdminLayout /></ProtectedRoute>}>
        <Route path="admin" element={<Navigate to="/admin/ops" replace />} />
        <Route path="admin/ops" element={<OpsOverviewPage />} />
        <Route path="admin/ops/tender" element={<OpsTenderPage />} />
        <Route path="admin/ops/collusion" element={<OpsCollusionPage />} />
        <Route path="admin/ops/writer" element={<OpsWriterPage />} />
        <Route path="admin/settings" element={<SettingsPage />} />
        <Route path="admin/users" element={<UserManagementPage />} />
        <Route path="admin/error-logs" element={<ErrorLogPage />} />
        <Route path="admin/projects/:projectId/timeline" element={<ProjectTimelinePage />} />
        <Route path="admin/audit-logs" element={<AuditLogPage />} />
        <Route path="admin/feedback" element={<FeedbackManagementPage />} />
        <Route path="admin/broadcasts" element={<AdminBroadcastsPage />} />
        {isLlmCenterEnabled && <Route path="admin/llm-center" element={<LlmCenterPage />} />}
      </Route>

      {/* 404 */}
      <Route path="*" element={<PublicLayout><NotFoundPage /></PublicLayout>} />
    </Routes>
    </Suspense>
    </TooltipProvider>
  )
}

export default App

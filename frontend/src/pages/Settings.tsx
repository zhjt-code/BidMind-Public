import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { APP_HOME } from '@/config/routes'
import { toast } from 'sonner'
import {
  settingsService,
  type AISettingsData,
  type ModelConfigData,
  type ModelPurpose,
} from '@/services/settingsApi'
import { SkeletonCard, ContextMenu } from '@/components'
import type { ContextMenuItem } from '@/components'
import { useConfigClipboardStore } from '@/stores'
import { sortConfigs } from '@/pages/settings/sortConfigs'
// P0-C: 配置弹窗组件化(三 Tab: 连接/能力与限制/生成约束)
import ModelConfigModal from '@/pages/settings/modelConfigModal/ModelConfigModal'

// ==================== 优先级常量 ====================

const PRIORITY_OPTIONS = [
  { value: 0,   label: '默认首选',  badge: '默认', badgeClass: 'bg-blue-100 text-blue-700' },
  { value: 50,  label: '次选',      badge: '次选', badgeClass: 'bg-cyan-100 text-cyan-700' },
  { value: 100, label: '普通',      badge: '',     badgeClass: '' },
  { value: 200, label: '备用',      badge: '备用', badgeClass: 'bg-amber-100 text-amber-700' },
  { value: 999, label: '末位备选',  badge: '末位', badgeClass: 'bg-gray-100 text-gray-500' },
] as const

// 单点档位（最多1个）：与后端 PRIORITIES_SINGLETON 保持一致
const PRIORITY_SINGLETON_VALUES: ReadonlySet<number> = new Set([0, 50, 200, 999])

// ==================== 子组件: 模型配置卡片 ====================

interface ConfigCardProps {
  config: ModelConfigData
  isInPool: boolean
  isTesting: boolean
  priority: number
  onTogglePool: () => void
  onPriorityChange: (p: number) => void
  onTest: () => void
  onEdit: () => void
  onDelete: () => void
  onContextMenu: (e: React.MouseEvent) => void
}

const ConfigCard: React.FC<ConfigCardProps> = ({
  config, isInPool, isTesting, priority, onTogglePool, onPriorityChange, onTest, onEdit, onDelete, onContextMenu,
}) => {
  const [showPriority, setShowPriority] = useState(false)
  const currentOption = PRIORITY_OPTIONS.find(o => o.value === priority) || PRIORITY_OPTIONS[1]

  return (
    <div
      onClick={onTogglePool}
      onContextMenu={onContextMenu}
      className={`p-4 border-2 rounded-lg cursor-pointer transition-all ${
        isInPool ? 'border-primary bg-blue-50' : 'border-gray-200 hover:border-gray-300'
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center">
          <div className={`w-4 h-4 rounded border-2 mr-3 flex items-center justify-center ${
            isInPool ? 'border-primary bg-primary' : 'border-gray-300'
          }`}>
            {isInPool && (
              <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </div>
          <span className="font-medium text-gray-900">{config.model_name}</span>
          {isInPool && currentOption.badge && (
            <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded font-medium ${currentOption.badgeClass}`}>
              {currentOption.badge}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {/* P0-C: 能力未验证提示(加入调度池前建议先「保存并验证」) */}
          {!config.capabilities?.observed_at && (
            <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-500" title="尚未执行能力验证">
              未验证
            </span>
          )}
          <span className={`text-xs px-2 py-0.5 rounded ${
            config.api_key_configured ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
          }`}>
            {config.api_key_configured
              ? (config.key_count > 1 ? `${config.key_count} 个密钥` : '密钥已配置')
              : '未配置密钥'}
          </span>
        </div>
      </div>
      <p className="text-xs text-gray-400 mb-3">{config.api_base}</p>
      <div className="flex items-center space-x-3">
        {isInPool && <span className="text-xs text-primary font-medium">调度中</span>}
        {isInPool && (
          <div className="relative">
            <button
              onClick={(e) => { e.stopPropagation(); setShowPriority(!showPriority) }}
              className="text-xs text-gray-500 hover:text-gray-700 border border-gray-300 rounded px-1.5 py-0.5"
            >
              {currentOption.label}
            </button>
            {showPriority && (
              <div className="absolute z-10 mt-1 left-0 bg-white border rounded-lg shadow-lg py-1 min-w-[100px]">
                {PRIORITY_OPTIONS.map(opt => (
                  <button key={opt.value}
                    onClick={(e) => { e.stopPropagation(); onPriorityChange(opt.value); setShowPriority(false) }}
                    className={`block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 ${
                      opt.value === priority ? 'text-primary font-medium' : 'text-gray-700'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <button
          onClick={(e) => { e.stopPropagation(); onTest() }}
          disabled={isTesting}
          className="text-sm text-primary hover:text-blue-600 disabled:text-gray-400"
        >
          {isTesting ? '测试中...' : '测试连接'}
        </button>
        <button onClick={(e) => { e.stopPropagation(); onEdit() }}
          className="text-sm text-gray-500 hover:text-gray-700">编辑</button>
        <button onClick={(e) => { e.stopPropagation(); onDelete() }}
          className="text-sm text-red-500 hover:text-red-700">删除</button>
      </div>
    </div>
  )
}

// ==================== 子组件: 模型区域 ====================

interface ModelSectionProps {
  title: string
  description: string
  purpose: ModelPurpose
  poolIds: string[]
  poolPriority: Record<string, number>
  configs: ModelConfigData[]
  onReload: () => void
}

const ModelSection: React.FC<ModelSectionProps> = ({
  title, description, purpose, poolIds, poolPriority, configs, onReload,
}) => {
  const [testingId, setTestingId] = useState<string | null>(null)
  // saving 仅用于串行化调度池/优先级/粘贴操作(防重复提交), UI 不消费其值
  const [, setSaving] = useState(false)
  const [modalVisible, setModalVisible] = useState(false)
  const [editingConfig, setEditingConfig] = useState<ModelConfigData | null>(null)

  // 右键菜单状态
  const [menu, setMenu] = useState<{ x: number; y: number; items: ContextMenuItem[] } | null>(null)
  const closeMenu = useCallback(() => setMenu(null), [])
  const clipboardItem = useConfigClipboardStore(s => s.item)
  const copyToClipboard = useConfigClipboardStore(s => s.copy)
  const clearClipboard = useConfigClipboardStore(s => s.clear)

  const localConfigs = sortConfigs(
    configs.filter(c => c.config_type === 'local'),
    poolIds,
    poolPriority,
  )
  const cloudConfigs = sortConfigs(
    configs.filter(c => c.config_type === 'cloud'),
    poolIds,
    poolPriority,
  )
  const totalFull = localConfigs.length >= 2 && cloudConfigs.length >= 5

  // 切换调度池
  const handleTogglePool = async (configId: string) => {
    setSaving(true)
    try {
      const newPoolIds = poolIds.includes(configId)
        ? poolIds.filter(id => id !== configId)
        : [...poolIds, configId]
      await settingsService.setPool(purpose, newPoolIds)
      toast.success(newPoolIds.includes(configId) ? '已加入调度池' : '已移出调度池')
      onReload()
    } catch (err) {
      toast.error(`操作失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  // 设置优先级（单点档位互斥，自动把同档旧条目降为"普通"）
  const handlePriorityChange = async (configId: string, newPriority: number) => {
    setSaving(true)
    try {
      const updated = { ...poolPriority }
      if (PRIORITY_SINGLETON_VALUES.has(newPriority)) {
        for (const [id, p] of Object.entries(updated)) {
          if (p === newPriority && id !== configId) {
            updated[id] = 100
          }
        }
      }
      updated[configId] = newPriority
      await settingsService.setPoolPriority(purpose, updated)
      toast.success('优先级已更新')
      onReload()
    } catch (err) {
      toast.error(`设置优先级失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  // 测试连接
  const handleTest = async (configId: string) => {
    setTestingId(configId)
    try {
      const result = await settingsService.testConnection(purpose, configId)
      const { status, results } = result.data
      if (status === 'connected') {
        const cnt = results?.length || 1
        toast.success(cnt > 1 ? `全部 ${cnt} 个密钥连接正常` : '连接正常')
      } else if (status === 'partial') {
        const failed = results?.filter(r => r.status !== 'connected') || []
        const passed = (results?.length || 0) - failed.length
        const failInfo = failed.map(r => `${r.label}: ${r.error || '失败'}`).join('; ')
        toast.error(`${passed}/${results?.length || 0} 通过。失败: ${failInfo}`)
      } else {
        const failed = results?.filter(r => r.status !== 'connected') || []
        if (failed.length > 0) {
          const failInfo = failed.map(r => `${r.label}: ${r.error || '失败'}`).join('; ')
          toast.error(`连接失败: ${failInfo}`)
        } else {
          toast.error(`连接失败: ${result.data.error || '未知错误'}`)
        }
      }
    } catch (err) {
      toast.error(`测试失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally { setTestingId(null) }
  }

  // 弹窗操作(表单状态与保存逻辑收敛在 ModelConfigModal 内)
  const openAdd = () => {
    setEditingConfig(null)
    setModalVisible(true)
  }
  const openEdit = (configId: string) => {
    const c = configs.find(x => x.id === configId)
    if (!c) return
    setEditingConfig(c)
    setModalVisible(true)
  }
  const closeModal = () => {
    setModalVisible(false)
    setEditingConfig(null)
  }
  const handleDelete = async (configId: string) => {
    try {
      await settingsService.deleteConfig(purpose, configId)
      toast.success('配置已删除'); onReload()
    } catch (err) {
      toast.error(`删除失败: ${err instanceof Error ? err.message : '未知错误'}`)
    }
  }

  // 右键复制 -- 卡片
  const handleCardContextMenu = (e: React.MouseEvent, c: ModelConfigData) => {
    e.preventDefault()
    e.stopPropagation()
    setMenu({
      x: e.clientX,
      y: e.clientY,
      items: [
        {
          key: 'copy',
          label: `复制配置: ${c.model_name}`,
          onClick: () => {
            copyToClipboard({
              sourceConfigId: c.id,
              sourcePurpose: purpose,
              sourceConfigType: c.config_type,
              modelName: c.model_name,
            })
            toast.success(`已复制配置: ${c.model_name}`)
          },
        },
      ],
    })
  }

  // 右键粘贴 -- 空白区
  const handleSectionContextMenu = (e: React.MouseEvent) => {
    e.preventDefault()
    const item = clipboardItem
    let pasteItem: ContextMenuItem
    if (!item) {
      pasteItem = { key: 'paste', label: '粘贴（剪贴板为空）', disabled: true }
    } else {
      const targetCount = item.sourceConfigType === 'local' ? localConfigs.length : cloudConfigs.length
      const targetMax = item.sourceConfigType === 'local' ? 2 : 5
      if (targetCount >= targetMax) {
        const typeLabel = item.sourceConfigType === 'local' ? '本地' : '云端'
        pasteItem = { key: 'paste', label: `粘贴（${typeLabel}模型已达上限 ${targetMax}）`, disabled: true }
      } else {
        pasteItem = {
          key: 'paste',
          label: `粘贴: ${item.modelName}`,
          onClick: () => handlePaste(item),
        }
      }
    }
    setMenu({ x: e.clientX, y: e.clientY, items: [pasteItem] })
  }

  const handlePaste = async (item: { sourceConfigId: string; sourcePurpose: ModelPurpose; modelName: string }) => {
    setSaving(true)
    try {
      const data = await settingsService.duplicateConfig(purpose, {
        source_purpose: item.sourcePurpose,
        source_config_id: item.sourceConfigId,
      })
      toast.success(`已粘贴: ${data.model_name}`)
      onReload()
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 404) {
        clearClipboard()
        toast.error('源配置已被删除，剪贴板已清空')
      } else {
        toast.error(`粘贴失败: ${err instanceof Error ? err.message : '未知错误'}`)
      }
    } finally {
      setSaving(false)
    }
  }

  // 渲染配置组
  const renderGroup = (label: string, items: ModelConfigData[], count: number, max: number) => (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-gray-600">{label}</span>
        <span className="text-xs text-gray-400">{count}/{max}</span>
      </div>
      {items.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {items.map(c => (
            <ConfigCard key={c.id} config={c}
              isInPool={poolIds.includes(c.id)} isTesting={testingId === c.id}
              priority={poolPriority[c.id] ?? 100}
              onTogglePool={() => handleTogglePool(c.id)}
              onPriorityChange={(p) => handlePriorityChange(c.id, p)}
              onTest={() => handleTest(c.id)}
              onEdit={() => openEdit(c.id)} onDelete={() => handleDelete(c.id)}
              onContextMenu={(e) => handleCardContextMenu(e, c)} />
          ))}
        </div>
      ) : (
        <p className="text-xs text-gray-400 py-2">暂无配置</p>
      )}
    </div>
  )

  return (
    <div className="bg-white rounded-lg shadow-sm mb-6">
      <ModelConfigModal visible={modalVisible} purpose={purpose}
        config={editingConfig}
        onSaved={() => { closeModal(); onReload() }} onCancel={closeModal} />

      <div className="px-6 py-4 border-b flex justify-between items-center">
        <div>
          <h2 className="text-lg font-medium text-gray-900">{title}</h2>
          <p className="text-sm text-gray-500 mt-1">{description}</p>
          {configs.length > 0 && (
            <p className={`text-xs mt-1 ${poolIds.length > 0 ? 'text-primary' : 'text-amber-600'}`}>
              {poolIds.length > 0
                ? (() => {
                    const preferred = poolIds.find(id => (poolPriority[id] ?? 100) === 0)
                    const preferredName = preferred ? configs.find(c => c.id === preferred)?.model_name : null
                    return preferredName
                      ? `已选 ${poolIds.length} 个模型参与调度，默认: ${preferredName}`
                      : `已选 ${poolIds.length} 个模型参与调度，系统自动负载均衡`
                  })()
                : '未选择调度模型，请勾选参与调度的配置'}
            </p>
          )}
        </div>
        <button onClick={openAdd} disabled={totalFull}
          className={`px-3 py-1.5 text-sm border rounded-md flex items-center ${
            totalFull ? 'text-gray-400 border-gray-300 cursor-not-allowed'
              : 'text-primary border-primary hover:bg-blue-50'
          }`} title={totalFull ? '已达上限' : '添加模型配置'}>
          <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          添加配置
        </button>
      </div>

      <div className="p-6" onContextMenu={handleSectionContextMenu}>
        {renderGroup('本地模型', localConfigs, localConfigs.length, 2)}
        {renderGroup('云端模型', cloudConfigs, cloudConfigs.length, 5)}

        {configs.length === 0 && (
          <div className="text-center py-6 text-gray-500">
            <p>暂无模型配置</p>
            <p className="text-sm mt-1">点击右上角"添加配置"添加本地或云端模型</p>
          </div>
        )}
      </div>

      {menu && (
        <ContextMenu x={menu.x} y={menu.y} items={menu.items} onClose={closeMenu} />
      )}
    </div>
  )
}

// ==================== 主组件 ====================

const SettingsPage: React.FC = () => {
  const navigate = useNavigate()
  const [aiSettings, setAiSettings] = useState<AISettingsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadSettings = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await settingsService.getAISettings()
      setAiSettings(data)
    } catch (err) {
      setError(`加载设置失败: ${err instanceof Error ? err.message : '未知错误'}`)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadSettings() }, [loadSettings])

  if (loading) {
    return (
      <div>
        <div className="h-8 w-24 bg-gray-200 rounded animate-pulse mb-6" />
        {[1, 2].map(i => (
          <div key={i} className="bg-white rounded-lg shadow-sm mb-6">
            <div className="px-6 py-4 border-b">
              <div className="h-5 w-32 bg-gray-200 rounded animate-pulse mb-2" />
              <div className="h-3 w-64 bg-gray-200 rounded animate-pulse" />
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-3">
              <SkeletonCard lines={2} hasAction />
              <SkeletonCard lines={2} hasAction />
            </div>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center mb-6">
        <button onClick={() => navigate(APP_HOME)}
          className="mr-4 p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-md transition-colors"
          title="返回项目列表">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
        </button>
        <div>
          <h1 className="text-2xl font-bold text-gray-900">系统设置</h1>
          <p className="text-gray-500 mt-1">配置AI模型和服务连接</p>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-4 bg-red-50 rounded-lg border border-red-200">
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {aiSettings && (
        <ModelSection
          title="视觉模型"
          description="处理fitz提取的图片，进行内容识别。勾选参与调度的模型（本地最多2个，云端最多5个）"
          purpose="vision"
          poolIds={aiSettings.vision_pool_ids}
          poolPriority={aiSettings.vision_pool_priority || {}}
          configs={aiSettings.vision_configs}
          onReload={loadSettings}
        />
      )}

      {aiSettings && (
        <ModelSection
          title="提取模型"
          description="机械类节点（提取/合并/分类/打标等）与 CoVe/Judge 复审共用；建议轻量模型并关闭思考。提取池为空时跳过复审，不会回落审查模型（本地最多2个，云端最多5个）"
          purpose="extract"
          poolIds={aiSettings.extract_pool_ids || []}
          poolPriority={aiSettings.extract_pool_priority || {}}
          configs={aiSettings.extract_configs || []}
          onReload={loadSettings}
        />
      )}

      {aiSettings && (
        <ModelSection
          title="审查模型"
          description="勾选参与调度的推理模型，系统自动负载均衡（本地最多2个，云端最多5个）"
          purpose="review"
          poolIds={aiSettings.review_pool_ids}
          poolPriority={aiSettings.review_pool_priority || {}}
          configs={aiSettings.review_configs}
          onReload={loadSettings}
        />
      )}

      {aiSettings && (
        <ModelSection
          title="评分模型"
          description="评分项提取 chat-PDF 直读救援专用池，放入支持 PDF 文件直传的模型（可右键复制审查池配置粘贴入池）；本池为空时自动回退使用审查模型池（本地最多2个，云端最多5个）"
          purpose="scoring"
          poolIds={aiSettings.scoring_pool_ids || []}
          poolPriority={aiSettings.scoring_pool_priority || {}}
          configs={aiSettings.scoring_configs || []}
          onReload={loadSettings}
        />
      )}

      <div className="p-4 bg-blue-50 rounded-lg border border-blue-200">
        <h3 className="text-sm font-medium text-blue-800 mb-2">使用说明</h3>
        <ul className="text-sm text-blue-700 space-y-1">
          <li>- <strong>添加配置</strong>: 填写API地址、密钥和模型名称，系统自动判断本地或云端</li>
          <li>- <strong>本地模型</strong>: API地址为 localhost 的自动归类为本地模型（如 Ollama），密钥可留空</li>
          <li>- <strong>云端模型</strong>: 其他地址自动归类为云端模型，需要填写API密钥</li>
          <li>- 视觉、提取、审查、评分模型各自独立配置；CoVe/Judge 复审走提取池</li>
          <li>- <strong>提取模型</strong>: 机械类节点与复审共用，建议轻量模型并关闭思考；提取池为空时跳过复审，不回落审查模型</li>
          <li>- <strong>评分模型</strong>: 评分项提取 chat-PDF 直读救援专用池，仅放入支持 PDF 直传的模型；留空时自动回退审查模型池</li>
          <li>- 添加配置后请先"测试连接"确认可用性，然后点击卡片选择使用该模型</li>
          <li>- <strong>调度优先级</strong>: 可为调度池中的模型设置优先级 -- "默认首选"模型空闲时优先使用，"末位备选"模型仅在其他模型不可用时启用</li>
        </ul>
      </div>
    </div>
  )
}

export default SettingsPage

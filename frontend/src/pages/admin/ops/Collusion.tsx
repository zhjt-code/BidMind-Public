import React from 'react'
import FlowFunnel from './components/FlowFunnel'
import ProjectLogsPanel from './components/ProjectLogsPanel'

const CollusionOps: React.FC = () => (
  <div className="space-y-6">
    <FlowFunnel
      flow="collusion"
      title="串标分析运行链路"
      subtitle="重点关注：样本规模、筛选效率、深度比对成本与风险分歧率"
    />
    <ProjectLogsPanel flow="collusion" />
  </div>
)

export default CollusionOps

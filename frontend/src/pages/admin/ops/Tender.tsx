import React from 'react'
import FlowFunnel from './components/FlowFunnel'
import ProjectLogsPanel from './components/ProjectLogsPanel'

const TenderOps: React.FC = () => (
  <div className="space-y-6">
    <FlowFunnel
      flow="tender"
      title="招标审查运行链路"
      subtitle="重点关注：要求提取完整性、匹配质量、审查吞吐与结论稳定性"
    />
    <ProjectLogsPanel flow="tender" />
  </div>
)

export default TenderOps

import React from 'react'
import FlowFunnel from './components/FlowFunnel'
import ProjectLogsPanel from './components/ProjectLogsPanel'

const WriterOps: React.FC = () => (
  <div className="space-y-6">
    <FlowFunnel
      flow="writer"
      title="标书编写运行链路"
      subtitle="重点关注：约束提取、目录生成、素材匹配与章节生成成本"
    />
    <ProjectLogsPanel flow="writer" />
  </div>
)

export default WriterOps

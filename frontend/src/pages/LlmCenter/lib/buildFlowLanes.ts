import type { LlmNodeSummary, LlmEdgeInfo } from '@/services/llmCenterApi'

export function buildFlowLanes(
  nodes: LlmNodeSummary[],
  edges: LlmEdgeInfo[],
): LlmNodeSummary[][] {
  const nodeKeys = new Set(nodes.map(n => n.node_key))
  const relevantEdges = edges.filter(e => nodeKeys.has(e.from_node_key) && nodeKeys.has(e.to_node_key))
  const outgoing = new Map<string, string[]>()
  const inDegree = new Map<string, number>()
  for (const n of nodes) { inDegree.set(n.node_key, 0) }
  for (const e of relevantEdges) {
    const arr = outgoing.get(e.from_node_key) || []
    arr.push(e.to_node_key)
    outgoing.set(e.from_node_key, arr)
    inDegree.set(e.to_node_key, (inDegree.get(e.to_node_key) || 0) + 1)
  }

  const nodeMap = new Map(nodes.map(n => [n.node_key, n]))
  const lanes: LlmNodeSummary[][] = []
  const remaining = new Set(nodeKeys)

  while (remaining.size > 0) {
    const roots = [...remaining].filter(k => (inDegree.get(k) || 0) === 0)
    if (roots.length === 0) {
      lanes.push([...remaining].map(k => nodeMap.get(k)!))
      break
    }
    lanes.push(roots.map(k => nodeMap.get(k)!))
    for (const r of roots) {
      remaining.delete(r)
      for (const to of (outgoing.get(r) || [])) {
        if (remaining.has(to)) {
          inDegree.set(to, (inDegree.get(to) || 0) - 1)
        }
      }
    }
  }

  return lanes
}

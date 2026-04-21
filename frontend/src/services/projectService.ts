import { getKgSummary, getRequirements, getStartupStatus, getTestCases } from '../api/backend'

const QUALITY_CATEGORIES = [
  '功能性',
  '性能效率',
  '兼容性',
  '易用性',
  '可靠性',
  '信息安全性',
  '维护性',
  '可移植性',
]

export type CategorySummaryItem = {
  分类: string
  需求数: number
  用例数: number
}

export type ProjectStatistics = {
  testcases: { total: number; passed: number; failed: number }
  executions: { total: number; passRate: number }
  integrations: { mcp: number }
  categorySummary: CategorySummaryItem[]
}

export async function getProjectStatistics(): Promise<ProjectStatistics> {
  const batchId = localStorage.getItem('current-batch-id') || undefined
  const [startup, requirements, cases, kg] = await Promise.all([
    getStartupStatus().catch(() => ({})),
    getRequirements(batchId).catch(() => []),
    getTestCases(batchId).catch(() => []),
    getKgSummary().catch(() => ({})),
  ])
  const passed = cases.filter((c) => String(c.system_env?.execution_status || '').toLowerCase() === 'passed').length
  const failed = cases.filter((c) => String(c.system_env?.execution_status || '').toLowerCase() === 'failed').length
  const totalExec = passed + failed
  const passRate = totalExec > 0 ? Math.round((passed / totalExec) * 100) : 0
  const modules = Array.isArray((kg as any).modules) ? (kg as any).modules : []
  void startup
  const reqCategory = new Map<string, string>()
  for (const r of requirements) {
    const c = String((r as any)?.ingestion_metadata?.quality_characteristic || '').trim() || '功能性'
    reqCategory.set(String((r as any).id || ''), c)
  }
  const categorySummary: CategorySummaryItem[] = QUALITY_CATEGORIES.map((name) => ({
    分类: name,
    需求数: 0,
    用例数: 0,
  }))
  const idx = new Map(categorySummary.map((r, i) => [r.分类, i]))
  for (const r of requirements) {
    const c = String((r as any)?.ingestion_metadata?.quality_characteristic || '').trim() || '功能性'
    const i = idx.get(c) ?? 0
    categorySummary[i].需求数 += 1
  }
  for (const c of cases) {
    const cat =
      String(c.system_env?.quality_characteristic || '').trim() ||
      reqCategory.get(String(c.related_req_id || '')) ||
      '功能性'
    const i = idx.get(cat) ?? 0
    categorySummary[i].用例数 += 1
  }

  return {
    testcases: { total: cases.length, passed, failed },
    executions: { total: totalExec, passRate },
    integrations: { mcp: modules.length },
    categorySummary,
  }
}

export type BackendHealth = { status?: string; [key: string]: unknown }

export type Requirement = {
  id: string
  original_text: string
  confidence?: number
  extracted_entities?: unknown
  ingestion_metadata?: Record<string, unknown>
}

export type IngestResponse = {
  saved: number
  files: string[]
  errors: Array<{ file?: string; error?: string }>
  replace_existing: boolean
  batch_id?: string
}

export type TestCase = {
  id?: number
  test_case_id: string
  related_req_id: string
  title: string
  priority?: string
  dimension?: string
  methodology?: string[]
  system_env?: Record<string, unknown>
  test_instruction?: {
    pre_condition?: string
    steps?: string[]
    expected_result?: string
  }
}

export type KgModuleSummary = {
  id?: string
  name?: string
  module?: string
  rules_count?: number
  scenarios_count?: number
  methods_count?: number
  templates_count?: number
  failure_modes_count?: number
  rules?: string[]
  scenarios?: Array<Record<string, unknown>>
  methods?: Array<Record<string, unknown>>
  templates?: Array<Record<string, unknown>>
  failure_modes?: Array<Record<string, unknown>>
  is_global?: boolean
  [key: string]: unknown
}

export type KgSummary = {
  modules: KgModuleSummary[]
  [key: string]: unknown
}

export type StreamUpdate = {
  type: string
  message?: string
  data?: unknown
  job_id?: string
  [key: string]: unknown
}

export type GenerateJobStatus = {
  job_id: string
  status: string
  done: boolean
  result_count: number
  error: string
  upload_batch_id?: string
  req_ids: string[]
  completed_at?: string
  next_index: number
  events: StreamUpdate[]
}

function shortenTechnicalDetail(raw: string, limit = 240): string {
  const text = String(raw || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return ''
  return text.length > limit ? `${text.slice(0, limit)}...` : text
}

function endpointLabel(url: string): string {
  if (url.startsWith('/requirements/ingest')) return '需求解析'
  if (url.startsWith('/requirements')) return '需求列表'
  if (url.startsWith('/test_cases')) return '用例列表'
  if (url.startsWith('/generate/stream')) return '生成任务'
  if (url.includes('/status')) return '生成进度'
  if (url.includes('/cancel')) return '取消生成'
  if (url.startsWith('/export/excel')) return 'Excel 导出'
  if (url.startsWith('/export/feishu-sheet')) return '飞书导出'
  if (url.startsWith('/health')) return '后端健康检查'
  if (url.startsWith('/startup-status')) return '启动检查'
  if (url.startsWith('/kg/summary')) return '知识图谱摘要'
  return '请求'
}

function timeoutHint(url: string): string {
  if (url.startsWith('/requirements')) {
    return '当前批次较大时，后端可能正在补充质量特性分类，请稍后重试。'
  }
  if (url.startsWith('/test_cases')) {
    return '当前批次用例较多或后端较忙，请稍后重试。'
  }
  if (url.includes('/status')) {
    return '生成任务可能仍在后台运行，请等待几秒后刷新进度。'
  }
  if (url.startsWith('/generate/stream')) {
    return '生成任务启动较慢时会出现该提示，请确认模型服务和后端状态正常。'
  }
  return '请确认后端服务可访问，并稍后重试。'
}

function statusHint(status: number): string {
  if (status === 400) return '请求参数不完整或格式不正确，请检查当前操作。'
  if (status === 404) return '目标数据不存在，可能已被删除或页面状态已过期。'
  if (status === 409) return '当前数据状态已变化，请刷新页面后重试。'
  if (status === 422) return '提交内容格式不符合要求，请检查输入项。'
  if (status === 500) return '后端处理时发生异常，请查看服务日志或稍后重试。'
  if (status === 502 || status === 503 || status === 504) return '后端服务暂时不可用，请确认后端和模型服务已启动。'
  return '请求未成功完成，请稍后重试。'
}

async function extractResponseDetail(resp: Response): Promise<string> {
  const text = await resp.text().catch(() => '')
  if (!text) return ''
  try {
    const payload = JSON.parse(text) as Record<string, unknown>
    const detail = [payload.message, payload.detail, payload.error]
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .join(' ')
    return detail || text
  } catch {
    return text
  }
}

function buildTimeoutError(url: string, timeoutMs: number): Error {
  const label = endpointLabel(url)
  return new Error(`${label}加载超时。\n${timeoutHint(url)}\n技术信息：${url} 超过 ${timeoutMs}ms 未返回。`)
}

function buildNetworkError(url: string, reason: string): Error {
  const label = endpointLabel(url)
  const technical = shortenTechnicalDetail(reason)
  return new Error(
    `${label}请求失败，当前无法连接后端服务。\n请确认后端已经启动，且前端代理地址配置正确。\n${technical ? `技术信息：${technical}` : ''}`.trim(),
  )
}

function buildHttpError(url: string, status: number, detail = ''): Error {
  const label = endpointLabel(url)
  const technical = shortenTechnicalDetail(detail)
  const lines = [`${label}请求失败。`, statusHint(status)]
  if (technical) {
    lines.push(`技术信息：HTTP ${status}，${technical}`)
  } else {
    lines.push(`技术信息：HTTP ${status}`)
  }
  return new Error(lines.join('\n'))
}

async function requestJson<T>(url: string, init?: RequestInit, timeoutMs = 10000): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  let resp: Response
  try {
    resp = await fetch(`/api${url}`, {
      ...init,
      signal: init?.signal ?? controller.signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      throw buildTimeoutError(url, timeoutMs)
    }
    throw buildNetworkError(url, e instanceof Error ? e.message : String(e))
  } finally {
    window.clearTimeout(timer)
  }
  if (!resp.ok) {
    const detail = await extractResponseDetail(resp)
    throw buildHttpError(url, resp.status, detail)
  }
  return (await resp.json()) as T
}

export function getHealth(): Promise<BackendHealth> {
  return requestJson<BackendHealth>('/health', undefined, 5000)
}

export function getStartupStatus(): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/startup-status', undefined, 5000)
}

export function getRequirements(batchId?: string): Promise<Requirement[]> {
  const query = batchId ? `/requirements?batch_id=${encodeURIComponent(batchId)}` : '/requirements'
  return requestJson<Requirement[]>(query, undefined, 8000)
}

export function getTestCases(batchId?: string): Promise<TestCase[]> {
  const query = batchId ? `/test_cases?batch_id=${encodeURIComponent(batchId)}` : '/test_cases'
  return requestJson<TestCase[]>(query, undefined, 8000)
}

export async function getKgSummary(): Promise<KgSummary> {
  const raw = await requestJson<unknown>('/kg/summary', undefined, 4000)
  if (Array.isArray(raw)) {
    return { modules: raw as KgModuleSummary[] }
  }
  const obj = (raw || {}) as Record<string, unknown>
  const modules = Array.isArray(obj.modules) ? (obj.modules as KgModuleSummary[]) : []
  return { ...obj, modules }
}

export async function streamGenerateCases(
  reqs: Requirement[],
  jobId: string,
  onUpdate: (u: StreamUpdate) => void,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(`/api/generate/stream?job_id=${encodeURIComponent(jobId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqs),
      signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      throw e
    }
    throw buildNetworkError('/generate/stream', e instanceof Error ? e.message : String(e))
  }
  if (!resp.ok || !resp.body) {
    const detail = await extractResponseDetail(resp)
    throw buildHttpError('/generate/stream', resp.status, detail)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const chunk of parts) {
      const line = chunk
        .split('\n')
        .find((l) => l.startsWith('data:'))
      if (!line) continue
      const payload = line.slice(5).trim()
      if (!payload) continue
      try {
        onUpdate(JSON.parse(payload) as StreamUpdate)
      } catch {
        // ignore malformed events
      }
    }
  }
}

export async function cancelGenerateJob(jobId: string): Promise<void> {
  let resp: Response
  try {
    resp = await fetch(`/api/generate/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
  } catch (e) {
    throw buildNetworkError('/generate/cancel', e instanceof Error ? e.message : String(e))
  }
  if (!resp.ok) {
    const detail = await extractResponseDetail(resp)
    throw buildHttpError('/generate/cancel', resp.status, detail)
  }
}

export function getGenerateJobStatus(jobId: string, sinceIndex = 0): Promise<GenerateJobStatus> {
  return requestJson<GenerateJobStatus>(`/generate/${encodeURIComponent(jobId)}/status?since_index=${sinceIndex}`, undefined, 8000)
}

export async function ingestRequirements(files: File[], replaceExisting = false): Promise<IngestResponse> {
  const fd = new FormData()
  files.forEach((f) => fd.append('files', f))
  fd.append('replace_existing', String(replaceExisting))
  let resp: Response
  try {
    resp = await fetch('/api/requirements/ingest', { method: 'POST', body: fd })
  } catch (e) {
    throw buildNetworkError('/requirements/ingest', e instanceof Error ? e.message : String(e))
  }
  if (!resp.ok) {
    let detail = ''
    try {
      const payload = (await resp.json()) as Record<string, unknown>
      const message = String(payload.message || payload.detail || payload.error || '').trim()
      const errors = Array.isArray(payload.errors) ? payload.errors : []
      const errorText = errors
        .map((e) => {
          const item = e as Record<string, unknown>
          return `${String(item.file || '文件')}: ${String(item.error || '未知错误')}`
        })
        .join('\n')
      detail = [message, errorText].filter(Boolean).join('\n')
    } catch {
      detail = await resp.text().catch(() => '')
    }
    throw buildHttpError('/requirements/ingest', resp.status, detail)
  }
  return (await resp.json()) as IngestResponse
}

export async function exportCasesExcel(caseIds: string[]): Promise<Blob> {
  let resp: Response
  try {
    resp = await fetch('/api/export/excel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case_ids: caseIds }),
    })
  } catch (e) {
    throw buildNetworkError('/export/excel', e instanceof Error ? e.message : String(e))
  }
  if (!resp.ok) {
    const detail = await extractResponseDetail(resp)
    throw buildHttpError('/export/excel', resp.status, detail)
  }
  return await resp.blob()
}

export type FeishuSheetPayload = {
  case_ids: string[]
  requirement_link_base_url: string
  app_id: string
  app_secret: string
  tenant_access_token: string
  base_url: string
  spreadsheet_token: string
  sheet_id: string
  start_cell: string
  auto_create_sheet: boolean
  sheet_title: string
  sheet_folder_token: string
}

export async function exportCasesToFeishuSheet(payload: FeishuSheetPayload): Promise<Record<string, unknown>> {
  let resp: Response
  try {
    resp = await fetch('/api/export/feishu-sheet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch (e) {
    throw buildNetworkError('/export/feishu-sheet', e instanceof Error ? e.message : String(e))
  }
  if (!resp.ok) {
    const detail = await extractResponseDetail(resp)
    throw buildHttpError('/export/feishu-sheet', resp.status, detail)
  }
  return (await resp.json()) as Record<string, unknown>
}

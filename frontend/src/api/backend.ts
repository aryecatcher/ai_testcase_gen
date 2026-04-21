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
      throw new Error(`${url} timeout after ${timeoutMs}ms`)
    }
    throw e
  } finally {
    window.clearTimeout(timer)
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`${url} failed: ${resp.status} ${text}`)
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
  const resp = await fetch(`/api/generate/stream?job_id=${encodeURIComponent(jobId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(reqs),
    signal,
  })
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '')
    throw new Error(`/generate/stream failed: ${resp.status} ${text}`)
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
  const resp = await fetch(`/api/generate/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`cancel failed: ${resp.status} ${text}`)
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
    const msg = e instanceof Error ? e.message : String(e)
    throw new Error(`上传或解析请求失败，请检查后端服务连接。\n${msg}`)
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
    throw new Error(`/requirements/ingest failed: ${resp.status}\n${detail}`)
  }
  return (await resp.json()) as IngestResponse
}

export async function exportCasesExcel(caseIds: string[]): Promise<Blob> {
  const resp = await fetch('/api/export/excel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ case_ids: caseIds }),
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`/export/excel failed: ${resp.status} ${text}`)
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
  const resp = await fetch('/api/export/feishu-sheet', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`/export/feishu-sheet failed: ${resp.status} ${text}`)
  }
  return (await resp.json()) as Record<string, unknown>
}

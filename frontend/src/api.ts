export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/api${path}`, init)
  } catch {
    throw new ApiError('Could not reach the local API. Check that the backend is running.', 0)
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: { message?: string; code?: string }; detail?: unknown } | null
    const detail = payload?.error ?? (typeof payload?.detail === 'object' && payload.detail !== null ? payload.detail as { message?: string; code?: string } : undefined)
    const message = detail?.message ?? `The API returned HTTP ${response.status}.`
    throw new ApiError(message, response.status, detail?.code)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export type Job = { status: string; retry_count: number; attempt_count: number; permanent_failure: boolean; error_details?: string | null }
export type DocumentSummary = {
  id: number; filename: string; status: string; created_at: string; updated_at: string
  invoice_number?: string | null; supplier_name?: string | null; customer_name?: string | null
  invoice_date?: string | null; currency?: string | null; validation_status?: string | null
  validation_error_count: number; error_details?: string | null; job?: Job | null
  correction_status?: 'pending' | 'needs_review' | 'ai_corrected' | 'human_corrected' | 'not_needed'
}
export type Page<T> = { total: number; page: number; page_size: number; items: T[] }
export type InvoiceLine = { id?: number; description: string; quantity?: string | null; unit_price?: string | null; amount?: string | null; line_total?: string | null }
export type Invoice = {
  id: number; document_id: number; invoice_number?: string | null; invoice_date?: string | null
  vendor_name?: string | null; customer_name?: string | null; currency?: string | null
  subtotal?: string | null; tax_amount?: string | null; total_amount?: string | null
  field_confidence?: Record<string, number | null> | null; raw_extraction?: Record<string, unknown> | null
  line_items: InvoiceLine[]; validation_errors: ValidationFinding[]
}
export type ValidationFinding = { id?: number; code: string; field?: string; field_name?: string; severity: string; message: string; explanation?: string; expected_value?: unknown; observed_value?: unknown }
export type HistoryEvent = { id: number; event_type: string; status?: string | null; details?: string | null; event_data?: Record<string, unknown> | null; created_at: string }
export type CorrectionEvent = { id: number; document_id: number; field_name: string; previous_value: unknown; corrected_value: unknown; corrected_by?: string | null; correction_source?: string; reason?: string | null; created_at: string }
export type AISuggestion = { field: string; current_value: unknown; suggested_value: unknown; evidence: string[] }
export type AISuggestionResponse = { document_id: number; status: 'suggestions_ready' | 'no_changes'; provider: string; model: string; suggestions: AISuggestion[] }

export function formatMoney(value: string | null | undefined, currency?: string | null): string {
  if (value == null || value === '') return 'Not found'
  return `${currency ?? ''} ${value}`.trim()
}

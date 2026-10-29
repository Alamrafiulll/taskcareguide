import { ChangeEvent, DragEvent, FormEvent, useCallback, useEffect, useState } from 'react'
import { API_BASE, api, AISuggestionResponse, CorrectionEvent, DocumentSummary, HistoryEvent, Invoice, InvoiceLine, Page, ValidationFinding } from './api'

type View = 'documents' | 'search' | 'compare'
type Readiness = { status: string; checks: Record<string, string> }
type UploadItem = { document_id?: number | null; filename: string; status: string; error?: { code?: string; message?: string } | null; duplicate_of_document_id?: number | null }
type CompareReport = { comparable: boolean; review_recommended: boolean; matched_fields: Array<Record<string, unknown>>; mismatched_fields: Array<Record<string, unknown>>; missing_fields: Array<Record<string, unknown>>; reasons: string[] }
const supported = new Set(['.pdf', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.xlsx', '.xls', '.csv'])
const pageSize = 10

function errorText(error: unknown): string { return error instanceof Error ? error.message : 'The request could not be completed.' }
function invoiceWarnings(invoice: Invoice | null): ValidationFinding[] {
  const raw = invoice?.raw_extraction?.invoice as { warnings?: ValidationFinding[] } | undefined
  return raw?.warnings ?? []
}
function progressText(row: DocumentSummary): string {
  if (row.status !== 'processing') return row.status === 'queued' ? 'Waiting for the worker' : row.status
  return 'Worker is processing this document'
}
function auditValue(value: unknown): string {
  if (value && typeof value === 'object' && 'value' in value) return String((value as { value?: unknown }).value ?? '—')
  return typeof value === 'string' ? value : JSON.stringify(value ?? '—')
}

export default function App() {
  const [view, setView] = useState<View>('documents')
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [documents, setDocuments] = useState<Page<DocumentSummary> | null>(null)
  const [page, setPage] = useState(1)
  const [filterForm, setFilterForm] = useState({ keyword: '', supplier: '', status: '', validation_status: '', correction_status: '' })
  const [appliedFilters, setAppliedFilters] = useState(filterForm)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [metadata, setMetadata] = useState<DocumentSummary | null>(null)
  const [invoice, setInvoice] = useState<Invoice | null>(null)
  const [history, setHistory] = useState<HistoryEvent[]>([])
  const [correctionHistory, setCorrectionHistory] = useState<CorrectionEvent[]>([])
  const [findings, setFindings] = useState<ValidationFinding[]>([])
  const [loadingList, setLoadingList] = useState(true)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [dragging, setDragging] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([])
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [lineItems, setLineItems] = useState<InvoiceLine[]>([])
  const [reason, setReason] = useState('')
  const [correctionSource, setCorrectionSource] = useState<'human' | 'local_model'>('human')
  const [aiSuggestions, setAiSuggestions] = useState<AISuggestionResponse | null>(null)
  const [selectedSuggestions, setSelectedSuggestions] = useState<string[]>([])
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [saving, setSaving] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMode, setSearchMode] = useState('keyword')
  const [searchResults, setSearchResults] = useState<DocumentSummary[]>([])
  const [searchScores, setSearchScores] = useState<Record<number, number | null>>({})
  const [searching, setSearching] = useState(false)
  const [compareA, setCompareA] = useState('')
  const [compareB, setCompareB] = useState('')
  const [compareDocuments, setCompareDocuments] = useState<DocumentSummary[]>([])
  const [comparison, setComparison] = useState<CompareReport | null>(null)
  const [comparing, setComparing] = useState(false)

  const loadDocuments = useCallback(async (currentPage = page, filters = appliedFilters) => {
    const params = new URLSearchParams({ page: String(currentPage), page_size: String(pageSize) })
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
    const result = await api<Page<DocumentSummary>>(`/documents?${params}`)
    setDocuments(result)
  }, [appliedFilters, page])

  const loadDetail = useCallback(async (id: number, quiet = false) => {
    if (!quiet) setLoadingDetail(true)
    try {
      const [meta, invoiceResult, historyResult, validationResult, corrections] = await Promise.all([
        api<DocumentSummary>(`/documents/${id}`),
        api<Invoice>(`/documents/${id}/invoice`).catch(() => null),
        api<HistoryEvent[]>(`/documents/${id}/history`),
        api<ValidationFinding[]>(`/documents/${id}/validation-errors`),
        api<CorrectionEvent[]>(`/documents/${id}/invoice/corrections`),
      ])
      setMetadata(meta)
      setInvoice(invoiceResult)
      setHistory(historyResult)
      setCorrectionHistory(corrections)
      setFindings(validationResult)
      const extracted = (invoiceResult?.raw_extraction?.invoice ?? {}) as Record<string, unknown>
      const fields = ['invoice_number', 'invoice_date', 'supplier_name', 'customer_name', 'currency', 'subtotal', 'tax_amount', 'total_amount']
      if (!quiet) {
        setEdit(Object.fromEntries(fields.map((field) => [field, String(extracted[field] ?? (field === 'supplier_name' ? invoiceResult?.vendor_name : field === 'customer_name' ? invoiceResult?.customer_name : (invoiceResult as unknown as Record<string, unknown> | null)?.[field]) ?? '')])))
        setLineItems((invoiceResult?.line_items ?? []).map((item) => ({ ...item })))
      }
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      if (!quiet) setLoadingDetail(false)
    }
  }, [])

  const refresh = useCallback(async () => {
    try {
      await loadDocuments()
      setError('')
    } catch (cause) { setError(errorText(cause)) }
  }, [loadDocuments])

  useEffect(() => {
    setLoadingList(true)
    void refresh().finally(() => setLoadingList(false))
    const timer = window.setInterval(() => { void refresh(); if (selectedId !== null) void loadDetail(selectedId, true) }, 4000)
    const readyTimer = window.setInterval(() => {
      void api<Readiness>('/ready').then(setReadiness).catch(() => setReadiness(null))
    }, 10000)
    void api<Readiness>('/ready').then(setReadiness).catch(() => setReadiness(null))
    return () => { window.clearInterval(timer); window.clearInterval(readyTimer) }
  }, [refresh, selectedId, loadDetail])

  useEffect(() => {
    if (view !== 'compare') return
    const params = new URLSearchParams({ page: '1', page_size: '100', status: 'completed' })
    void api<Page<DocumentSummary>>(`/documents?${params}`).then((result) => setCompareDocuments(result.items)).catch((cause) => setError(errorText(cause)))
  }, [view])

  useEffect(() => {
    if (selectedId !== null) void loadDetail(selectedId)
    else { setMetadata(null); setInvoice(null); setHistory([]); setCorrectionHistory([]); setFindings([]) }
  }, [selectedId, loadDetail])

  const pageCount = Math.max(1, Math.ceil((documents?.total ?? 0) / pageSize))
  const processingEvent = [...history].reverse().find((event) => event.event_type === 'progress')
  const evidence = (invoice?.raw_extraction?.invoice as { field_evidence?: Record<string, Array<{ snippet?: string; page?: number; sheet?: string; cell_coordinates?: string[] }>> } | undefined)?.field_evidence ?? {}
  const currentInvoiceFields = (invoice?.raw_extraction?.invoice ?? {}) as Record<string, unknown>
  const originalInvoiceFields = (invoice?.raw_extraction?.original_invoice ?? currentInvoiceFields) as Record<string, unknown>
  const changedInvoiceFields = [...new Set(correctionHistory.map((item) => item.field_name))]

  function reviewFieldValue(field: string, original: boolean): unknown {
    if (field === 'line_items') return original ? originalInvoiceFields.line_items : invoice?.line_items
    const values = original ? originalInvoiceFields : currentInvoiceFields
    if (field === 'supplier_name') return values.supplier_name ?? values.vendor_name ?? (original ? null : invoice?.vendor_name)
    if (field === 'customer_name') return values.customer_name ?? (original ? null : invoice?.customer_name)
    if (field === 'subtotal' || field === 'tax_amount' || field === 'total_amount' || field === 'invoice_number' || field === 'invoice_date' || field === 'currency') {
      return values[field] ?? (original ? null : (invoice as unknown as Record<string, unknown> | null)?.[field])
    }
    return values[field]
  }
  function readableValue(value: unknown): string {
    if (value == null || value === '') return 'Not found'
    return typeof value === 'object' ? JSON.stringify(value) : String(value)
  }

  function acceptFiles(input: FileList | File[]) {
    const incoming = Array.from(input)
    const rejected: UploadItem[] = []
    const valid: File[] = []
    for (const file of incoming) {
      const extension = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`
      if (!supported.has(extension)) rejected.push({ filename: file.name, status: 'rejected', error: { code: 'unsupported_format', message: 'Choose a PDF, JPG, PNG, TIFF, XLS, XLSX, or CSV file.' } })
      else if (file.size > 50 * 1024 * 1024) rejected.push({ filename: file.name, status: 'rejected', error: { code: 'file_too_large', message: 'This file exceeds the default 50 MiB limit. The server setting is authoritative.' } })
      else valid.push(file)
    }
    setFiles((previous) => [...previous, ...valid])
    setUploadItems(rejected)
    setError('')
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.currentTarget.files) acceptFiles(event.currentTarget.files)
    event.currentTarget.value = ''
  }
  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false)
    if (event.dataTransfer.files.length) acceptFiles(event.dataTransfer.files)
  }

  async function uploadFiles(event: FormEvent) {
    event.preventDefault()
    if (!files.length) return
    setUploading(true); setError(''); setNotice('')
    const body = new FormData()
    files.forEach((file) => body.append('files', file))
    try {
      const response = await api<{ accepted_count: number; rejected_count: number; items: UploadItem[] }>('/documents/upload', { method: 'POST', body })
      setUploadItems(response.items); setFiles([])
      setNotice(`${response.accepted_count} file${response.accepted_count === 1 ? '' : 's'} queued; ${response.rejected_count} rejected.`)
      setPage(1)
      await loadDocuments(1, appliedFilters)
      const first = response.items.find((item) => item.document_id && item.status === 'queued')
      if (first?.document_id) setSelectedId(first.document_id)
    } catch (cause) { setError(errorText(cause)) }
    finally { setUploading(false) }
  }

  async function submitCorrection(event: FormEvent) {
    event.preventDefault()
    if (!selectedId) return
    setSaving(true); setError(''); setNotice('')
    const payload: Record<string, unknown> = {
      reason: reason || null,
      corrected_by: correctionSource === 'local_model' ? 'Reviewer accepted local model suggestion' : 'Local reviewer',
      correction_source: correctionSource,
      correction_model: correctionSource === 'local_model' ? aiSuggestions?.model : null,
    }
    for (const key of ['invoice_number', 'invoice_date', 'supplier_name', 'customer_name', 'currency']) payload[key] = edit[key] || null
    for (const key of ['subtotal', 'tax_amount', 'total_amount']) payload[key] = edit[key] === '' ? null : edit[key]
    payload.line_items = lineItems.map(({ description, quantity, unit_price, amount, line_total }) => ({
      description, quantity: quantity || null, unit_price: unit_price || null, line_total: line_total ?? amount ?? null,
    }))
    try {
      await api<Invoice>(`/documents/${selectedId}/invoice`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      await loadDetail(selectedId); await loadDocuments()
      setCorrectionSource('human')
      setNotice('Correction saved. Validation was rerun and the audit history was updated.')
    } catch (cause) { setError(errorText(cause)) }
    finally { setSaving(false) }
  }

  async function requestAISuggestions() {
    if (!selectedId) return
    setLoadingSuggestions(true); setError(''); setAiSuggestions(null); setSelectedSuggestions([])
    try {
      const result = await api<AISuggestionResponse>(`/documents/${selectedId}/invoice/ai-suggestions`, { method: 'POST' })
      setAiSuggestions(result)
      setSelectedSuggestions(result.suggestions.map((item) => item.field))
      setNotice(result.status === 'no_changes' ? 'The local model found no evidence-grounded changes for fields flagged for review.' : `The local model proposed ${result.suggestions.length} evidence-grounded field changes. Review and choose before saving.`)
    } catch (cause) { setError(errorText(cause)) }
    finally { setLoadingSuggestions(false) }
  }

  function stageSelectedSuggestions() {
    if (!aiSuggestions) return
    const selected = aiSuggestions.suggestions.filter((item) => selectedSuggestions.includes(item.field))
    setEdit((current) => {
      const next = { ...current }
      for (const item of selected) {
        if (item.field === 'line_items') continue
        next[item.field] = String(item.suggested_value ?? '')
      }
      return next
    })
    const lineSuggestion = selected.find((item) => item.field === 'line_items')
    if (lineSuggestion && Array.isArray(lineSuggestion.suggested_value)) {
      setLineItems((lineSuggestion.suggested_value as Array<Record<string, unknown>>).map((item) => ({
        description: String(item.description ?? ''),
        quantity: item.quantity == null ? null : String(item.quantity),
        unit_price: item.unit_price == null ? null : String(item.unit_price),
        amount: item.line_total == null ? null : String(item.line_total),
      })))
    }
    setCorrectionSource('local_model')
    setReason(`Reviewed and accepted suggestions from ${aiSuggestions.model}`)
    setNotice('Selected values are staged in the correction form. Save them to update the stored result and audit history.')
  }

  async function retryDocument() {
    if (!selectedId) return
    setError(''); setNotice('')
    try {
      await api(`/documents/${selectedId}/retry`, { method: 'POST' })
      setNotice('Retry queued. The worker will pick it up; status refreshes automatically.')
      await loadDocuments()
      await loadDetail(selectedId)
    } catch (cause) { setError(errorText(cause)) }
  }

  async function runSearch(event: FormEvent) {
    event.preventDefault()
    setSearching(true); setError('')
    try {
      const params = new URLSearchParams({ query: searchQuery, mode: searchMode, page: '1', page_size: '25' })
      const response = await api<{ results: Array<{ document: DocumentSummary; score: number | null }> }>(`/search?${params}`)
      setSearchResults(response.results.map((item) => item.document))
      setSearchScores(Object.fromEntries(response.results.map((item) => [item.document.id, item.score])))
    } catch (cause) { setError(errorText(cause)); setSearchResults([]) }
    finally { setSearching(false) }
  }

  async function runComparison(event: FormEvent) {
    event.preventDefault()
    setComparing(true); setError(''); setComparison(null)
    try {
      const response = await api<CompareReport>('/documents/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_a_id: Number(compareA), document_b_id: Number(compareB) }) })
      setComparison(response)
    } catch (cause) { setError(errorText(cause)) }
    finally { setComparing(false) }
  }

  function changeFilter(key: keyof typeof filterForm, value: string) { setFilterForm((old) => ({ ...old, [key]: value })) }
  function applyFilters(event: FormEvent) { event.preventDefault(); setPage(1); setAppliedFilters({ ...filterForm }); setSelectedId(null) }
  function openCorrectionView(status: 'needs_review' | 'ai_corrected') {
    const next = { ...filterForm, status: '', validation_status: '', correction_status: status }
    setView('documents'); setFilterForm(next); setAppliedFilters(next); setPage(1); setSelectedId(null)
  }
  function markHumanCorrection() { setCorrectionSource('human') }
  function updateInvoiceField(field: string, value: string) {
    markHumanCorrection()
    setEdit((current) => ({ ...current, [field]: value }))
  }
  function updateLineItem(index: number, values: Partial<InvoiceLine>) {
    markHumanCorrection()
    setLineItems((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...values } : row))
  }
  function selectDocument(id: number) { setSelectedId(id); setError(''); setNotice('') }

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#documents" onClick={() => setView('documents')}><span className="brand-mark">cg</span><span>Care Guide<small>DOCUMENT INTELLIGENCE</small></span></a>
      <nav className="main-nav" aria-label="Workspace">
        <button className={view === 'documents' && !appliedFilters.correction_status ? 'active' : ''} onClick={() => { setView('documents'); setFilterForm({ ...filterForm, correction_status: '' }); setAppliedFilters({ ...appliedFilters, correction_status: '' }) }}>Documents</button>
        <button className={appliedFilters.correction_status === 'needs_review' ? 'active' : ''} onClick={() => openCorrectionView('needs_review')}>Needs correction</button>
        <button className={appliedFilters.correction_status === 'ai_corrected' ? 'active' : ''} onClick={() => openCorrectionView('ai_corrected')}>AI corrected</button>
        <button className={view === 'search' ? 'active' : ''} onClick={() => setView('search')}>Search</button>
        <button className={view === 'compare' ? 'active' : ''} onClick={() => setView('compare')}>Compare</button>
      </nav>
      <div className="connection" aria-label="Backend readiness"><span className={`status-dot ${readiness?.checks.database === 'ok' ? 'online' : 'offline'}`} /><span>{readiness?.checks.database === 'ok' ? 'API connected' : 'API unavailable'}</span><span className="worker-badge">Worker {readiness?.checks.worker === 'ok' ? 'online' : 'offline'}</span></div>
    </header>

    <main className="workspace">
      {(error || notice) && <div className={`banner ${error ? 'banner-error' : 'banner-success'}`} role={error ? 'alert' : 'status'}>
        <span>{error || notice}</span><button aria-label="Dismiss message" onClick={() => { setError(''); setNotice('') }}>×</button>
      </div>}

      {view === 'documents' && <>
        <div className="page-heading"><div><p className="eyebrow">LOCAL WORKSPACE</p><h1>Invoice review</h1><p className="subheading">Upload documents, track processing, and review extracted fields against their source.</p></div><div className="heading-meta"><span>{documents?.total ?? '—'} documents</span><span>SQLite · Local processing</span></div></div>
        <section className="upload-panel" aria-labelledby="upload-heading">
          <div className="section-title"><div><p className="eyebrow">INGEST</p><h2 id="upload-heading">Add documents</h2></div><span className="format-note">PDF · JPG · PNG · TIFF · XLS · XLSX · CSV</span></div>
          <form onSubmit={uploadFiles}>
            <div className={`dropzone ${dragging ? 'dropzone-active' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
              <div className="upload-icon" aria-hidden="true">↑</div><div><strong>Drop invoices here</strong><p>Choose multiple supported files. Uploads are validated on the server.</p></div>
              <label className="button button-secondary file-picker">Browse files<input aria-label="Choose invoice files" type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv" onChange={onFileChange} /></label>
            </div>
            {files.length > 0 && <div className="selected-files"><span>{files.length} file{files.length === 1 ? '' : 's'} selected</span><ul>{files.map((file, i) => <li key={`${file.name}-${i}`}>{file.name}<button type="button" aria-label={`Remove ${file.name}`} onClick={() => setFiles((items) => items.filter((_, index) => index !== i))}>Remove</button></li>)}</ul></div>}
            <div className="upload-actions"><small>Default maximum 50 MiB per file; server validation is authoritative.</small><button className="button button-primary" disabled={!files.length || uploading}>{uploading ? 'Uploading…' : 'Upload and queue'}</button></div>
          </form>
          {uploadItems.length > 0 && <ul className="upload-feedback" aria-label="Upload results">{uploadItems.map((item, i) => <li className={item.status === 'rejected' ? 'is-error' : ''} key={`${item.filename}-${i}`}><strong>{item.filename}</strong><span>{item.status === 'queued' ? `Queued · document ${item.document_id}` : item.status === 'duplicate' ? `Duplicate of document ${item.duplicate_of_document_id}` : item.error?.message ?? item.status}</span></li>)}</ul>}
        </section>

        <section className="documents-section" aria-labelledby="documents-heading">
          <div className="section-title"><div><p className="eyebrow">REVIEW QUEUE</p><h2 id="documents-heading">Documents</h2></div><button className="button button-quiet" onClick={() => void refresh()}>Refresh</button></div>
          <form className="filters" onSubmit={applyFilters}>
            <label>Keyword<input aria-label="Filter keyword" value={filterForm.keyword} onChange={(e) => changeFilter('keyword', e.target.value)} placeholder="Invoice number or text" /></label>
            <label>Supplier<input aria-label="Filter supplier" value={filterForm.supplier} onChange={(e) => changeFilter('supplier', e.target.value)} placeholder="Supplier name" /></label>
            <label>Status<select aria-label="Filter status" value={filterForm.status} onChange={(e) => changeFilter('status', e.target.value)}><option value="">All statuses</option><option value="queued">Queued</option><option value="processing">Processing</option><option value="completed">Completed</option><option value="failed">Failed</option></select></label>
            <label>Validation<select aria-label="Filter validation" value={filterForm.validation_status} onChange={(e) => changeFilter('validation_status', e.target.value)}><option value="">All validation</option><option value="valid">No findings</option><option value="has_errors">Has findings</option><option value="pending">Pending extraction</option></select></label>
            <label>Correction view<select aria-label="Filter correction view" value={filterForm.correction_status} onChange={(e) => changeFilter('correction_status', e.target.value)}><option value="">All documents</option><option value="needs_review">Needs correction</option><option value="ai_corrected">AI corrected</option><option value="human_corrected">Human corrected</option><option value="not_needed">No correction needed</option></select></label>
            <button className="button button-secondary">Apply filters</button>
          </form>
          {loadingList && !documents ? <div className="empty-state">Loading documents…</div> : documents?.items.length ? <>
            <div className="table-wrap"><table className="document-table"><thead><tr><th>Document</th><th>Invoice</th><th>Supplier</th><th>Date</th><th>Status</th><th>Review</th></tr></thead><tbody>
              {documents.items.map((row) => <tr key={row.id} className={selectedId === row.id ? 'row-selected' : ''}>
                <td><button className="document-link" onClick={() => selectDocument(row.id)}>{row.filename}<small>Document #{row.id}</small></button></td>
                <td>{row.invoice_number || '—'}</td><td>{row.supplier_name || '—'}</td><td>{row.invoice_date || '—'}</td>
                <td><span className={`status-tag status-${row.status}`}>{row.status}</span><small className="status-caption">{progressText(row)}</small></td>
                <td className="review-cell">{row.correction_status === 'ai_corrected' && <span className="review-tag ai-corrected-tag">AI corrected</span>}{row.correction_status === 'human_corrected' && <span className="review-tag">Human corrected</span>}{row.validation_status === 'has_errors' ? <span className="review-tag needs-review">{row.validation_error_count} finding{row.validation_error_count === 1 ? '' : 's'} · review</span> : row.validation_status === 'valid' && row.correction_status !== 'ai_corrected' && row.correction_status !== 'human_corrected' ? 'Clear' : row.validation_status === 'pending' ? 'Pending' : null}</td>
              </tr>)}
            </tbody></table></div>
            <div className="pagination"><span>{documents.total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, documents.total)} of ${documents.total}` : 'No results'}</span><div><button className="button button-quiet" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</button><span>Page {page} of {pageCount}</span><button className="button button-quiet" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>Next</button></div></div>
          </> : <div className="empty-state"><strong>No documents match these filters.</strong><span>Upload a document or adjust the filters above.</span></div>}
        </section>
        {selectedId !== null && <section className="detail-section" aria-labelledby="detail-heading">
          {loadingDetail ? <div className="empty-state">Loading document details…</div> : metadata && <>
            <div className="detail-header"><div><p className="eyebrow">DOCUMENT #{metadata.id}</p><h2 id="detail-heading">{metadata.filename}</h2><p className="subheading">{metadata.job ? `Attempt ${metadata.job.attempt_count} · ${metadata.job.status}` : metadata.status}</p></div><div className="detail-actions"><span className={`status-tag status-${metadata.status}`}>{metadata.status}</span>{metadata.status === 'failed' && <button className="button button-secondary" onClick={() => void retryDocument()} disabled={metadata.job?.permanent_failure}>Retry processing</button>}{invoice && <><a className="button button-quiet" href={`${API_BASE}/api/documents/${metadata.id}/result?view=original`} target="_blank" rel="noreferrer">Download original JSON</a><a className="button button-primary" href={`${API_BASE}/api/documents/${metadata.id}/result?view=corrected`} target="_blank" rel="noreferrer">Download current JSON</a></>}</div></div>
            {metadata.status === 'processing' && <div className="progress-note" role="status">{processingEvent ? `${processingEvent.details ?? 'Processing'}${processingEvent.event_data?.completed && processingEvent.event_data?.total ? ` · ${processingEvent.event_data.completed} of ${processingEvent.event_data.total}` : ''}` : 'The worker is processing this document.'} · updated from saved processing history</div>}
            {metadata.error_details && <div className="finding finding-error">{metadata.error_details}</div>}
            <div className="review-grid">
              <div className="card invoice-card"><div className="section-title"><div><p className="eyebrow">EXTRACTED DATA</p><h3>Invoice details</h3></div>{invoice && <span className={`review-tag ${findings.length ? 'needs-review' : 'clear'}`}>{findings.length ? 'Review required' : 'No findings'}</span>}</div>
                {invoice ? <form className="invoice-form" onSubmit={submitCorrection}>
                  <div className="field-grid">
                    <label>Invoice number<input value={edit.invoice_number ?? ''} onChange={(e) => updateInvoiceField('invoice_number', e.target.value)} /></label>
                    <label>Invoice date<input type="date" value={edit.invoice_date?.slice(0, 10) ?? ''} onChange={(e) => updateInvoiceField('invoice_date', e.target.value)} /></label>
                    <label>Supplier<input value={edit.supplier_name ?? ''} onChange={(e) => updateInvoiceField('supplier_name', e.target.value)} /></label>
                    <label>Customer<input value={edit.customer_name ?? ''} onChange={(e) => updateInvoiceField('customer_name', e.target.value)} /></label>
                    <label>Currency<input maxLength={3} value={edit.currency ?? ''} onChange={(e) => updateInvoiceField('currency', e.target.value.toUpperCase())} placeholder="USD" /></label>
                    <label>Subtotal<input inputMode="decimal" value={edit.subtotal ?? ''} onChange={(e) => updateInvoiceField('subtotal', e.target.value)} placeholder="Not found" /></label>
                    <label>Tax amount<input inputMode="decimal" value={edit.tax_amount ?? ''} onChange={(e) => updateInvoiceField('tax_amount', e.target.value)} placeholder="Not found" /></label>
                    <label>Total amount<input inputMode="decimal" value={edit.total_amount ?? ''} onChange={(e) => updateInvoiceField('total_amount', e.target.value)} placeholder="Not found" /></label>
                  </div>
                  <div className="line-items-heading"><h4>Line items</h4><button type="button" className="button button-quiet" onClick={() => { markHumanCorrection(); setLineItems((items) => [...items, { description: '', quantity: '', unit_price: '', amount: '' }]) }}>Add line</button></div>
                  {lineItems.length ? <div className="table-wrap"><table className="line-table"><thead><tr><th>Description</th><th>Quantity</th><th>Unit price</th><th>Line total</th><th /></tr></thead><tbody>{lineItems.map((item, i) => <tr key={item.id ?? i}>
                    <td><input aria-label={`Line ${i + 1} description`} value={item.description} onChange={(e) => updateLineItem(i, { description: e.target.value })} /></td>
                    <td><input aria-label={`Line ${i + 1} quantity`} inputMode="decimal" value={item.quantity ?? ''} onChange={(e) => updateLineItem(i, { quantity: e.target.value })} /></td>
                    <td><input aria-label={`Line ${i + 1} unit price`} inputMode="decimal" value={item.unit_price ?? ''} onChange={(e) => updateLineItem(i, { unit_price: e.target.value })} /></td>
                    <td><input aria-label={`Line ${i + 1} line total`} inputMode="decimal" value={item.amount ?? item.line_total ?? ''} onChange={(e) => updateLineItem(i, { amount: e.target.value })} /></td>
                    <td><button type="button" aria-label={`Remove line ${i + 1}`} className="icon-button" onClick={() => { markHumanCorrection(); setLineItems((rows) => rows.filter((_, index) => index !== i)) }}>×</button></td>
                  </tr>)}</tbody></table></div> : <p className="muted">No line items were extracted.</p>}
                  <label>Correction reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Optional: explain the source used" /></label>
                  <div className="save-row"><small>Saving records the previous value and reruns financial validation.</small><button className="button button-primary" disabled={saving}>{saving ? 'Saving…' : 'Save corrections'}</button></div>
                </form> : <div className="empty-state">Invoice extraction is not available until processing completes.</div>}
              </div>
              <aside className="side-stack">
                <div className="card"><p className="eyebrow">QUALITY REVIEW</p><h3>Validation & uncertainty</h3>
                  {findings.length || invoiceWarnings(invoice).length ? <ul className="finding-list">{[...findings, ...invoiceWarnings(invoice)].map((item, i) => <li key={`${item.code}-${i}`} className={`finding ${item.severity === 'error' ? 'finding-error' : 'finding-warning'}`}><strong>{item.field_name ?? item.field ?? item.code}</strong><span>{item.message}</span>{item.explanation && <small>{item.explanation}</small>}</li>)}</ul> : invoice ? <p className="muted">No stored validation findings.</p> : <p className="muted">Results appear after processing.</p>}
                  {invoice?.field_confidence && <div className="confidence-note">Field quality indicators are heuristic, not calibrated probabilities. {Object.entries(invoice.field_confidence).map(([key, value]) => value !== null && value < 0.7 ? <span className="review-tag needs-review" key={key}>{key.replace(/_/g, ' ')} · low quality</span> : null)}</div>}
                </div>
                <div className="card"><p className="eyebrow">OPTIONAL LOCAL MODEL</p><h3>Correction suggestions</h3>
                  {readiness?.checks.local_ai === 'enabled' ? <button className="button button-secondary" onClick={() => void requestAISuggestions()} disabled={loadingSuggestions}>{loadingSuggestions ? 'Reviewing source…' : 'Suggest corrections with Ollama'}</button> : <p className="muted">Local AI is disabled. Set OLLAMA_ENABLED=true and OLLAMA_MODEL in .env, then restart the API and worker to request suggestions. Manual review and correction remain available.</p>}
                  {aiSuggestions && <div className="suggestion-review" aria-label="Model correction suggestions"><p className="model-caption">Model: {aiSuggestions.model}. Suggestions are evidence-grounded proposals and are not saved until you stage and save them.</p>
                    {aiSuggestions.suggestions.length ? <ul className="suggestion-list">{aiSuggestions.suggestions.map((item) => <li key={item.field}><label className="suggestion-select"><input type="checkbox" checked={selectedSuggestions.includes(item.field)} onChange={(event) => setSelectedSuggestions((selected) => event.target.checked ? [...selected, item.field] : selected.filter((field) => field !== item.field))} /><strong>{item.field.replace(/_/g, ' ')}</strong></label><div className="suggestion-values"><span><small>Current</small><code>{readableValue(item.current_value)}</code></span><span><small>Model suggests</small><code>{readableValue(item.suggested_value)}</code></span></div><small>Source evidence</small>{item.evidence.map((snippet, index) => <blockquote key={`${item.field}-${index}`}>{snippet}</blockquote>)}</li>)}</ul> : <p className="muted">The model found no changes it could support from the source.</p>}
                    {aiSuggestions.suggestions.length > 0 && <button type="button" className="button button-primary" disabled={!selectedSuggestions.length} onClick={stageSelectedSuggestions}>Use selected suggestions in form</button>}
                  </div>}
                </div>
                <div className="card"><p className="eyebrow">BEFORE & AFTER</p><h3>Extracted and corrected values</h3>
                  {changedInvoiceFields.length ? <div className="before-after-list">{changedInvoiceFields.map((field) => <div key={field}><strong>{field.replace(/_/g, ' ')}</strong><span><small>Original</small><code>{readableValue(reviewFieldValue(field, true))}</code></span><span><small>Current result</small><code>{readableValue(reviewFieldValue(field, false))}</code></span></div>)}</div> : <p className="muted">No saved corrections yet. Original extraction and current result are the same.</p>}
                </div>
                <div className="card"><p className="eyebrow">SOURCE TRACE</p><h3>Evidence snippets</h3>{Object.entries(evidence).length ? <ul className="evidence-list">{Object.entries(evidence).flatMap(([field, refs]) => refs.map((ref, index) => <li key={`${field}-${index}`}><strong>{field.replace(/_/g, ' ')}</strong><blockquote>{ref.snippet || 'No text snippet stored.'}</blockquote><small>{ref.page ? `Page ${ref.page}` : ref.sheet ? `Sheet ${ref.sheet}` : 'Source'}{ref.cell_coordinates?.length ? ` · ${ref.cell_coordinates.join(', ')}` : ''}</small></li>))}</ul> : <p className="muted">No field-level source snippets were stored.</p>}</div>
                <div className="card"><p className="eyebrow">CORRECTION AUDIT</p><h3>Manual changes</h3>{correctionHistory.length ? <ol className="correction-list">{correctionHistory.map((item) => <li key={item.id}><strong>{item.field_name.replace(/_/g, ' ')}</strong><span><s>{auditValue(item.previous_value)}</s> <b>→</b> {auditValue(item.corrected_value)}</span>{item.reason && <small>{item.reason}</small>}<small>{new Date(item.created_at).toLocaleString()} · {item.corrected_by || 'Reviewer'}</small></li>)}</ol> : <p className="muted">No manual corrections recorded.</p>}</div>
                <div className="card"><p className="eyebrow">AUDIT TRAIL</p><h3>Processing history</h3>{history.length ? <ol className="history-list">{history.map((event) => <li key={event.id}><span className="history-dot" /><div><strong>{event.event_type.replace(/_/g, ' ')}</strong><small>{new Date(event.created_at).toLocaleString()}</small>{event.details && <p>{event.details}</p>}</div></li>)}</ol> : <p className="muted">No history recorded.</p>}</div>
              </aside>
            </div>
          </>}
        </section>}
      </>}

      {view === 'search' && <section className="tool-page"><div className="page-heading"><div><p className="eyebrow">FIND STORED RESULTS</p><h1>Search documents</h1><p className="subheading">Keyword search works offline. Semantic search requires the optional local model.</p></div></div>
        <form className="tool-form" onSubmit={runSearch}><label>Search query<input autoFocus value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Invoice number, supplier, or phrase" required /></label><label>Search mode<select value={searchMode} onChange={(e) => setSearchMode(e.target.value)}><option value="keyword">Keyword</option><option value="semantic">Semantic (local model)</option><option value="hybrid">Hybrid</option></select></label><button className="button button-primary" disabled={searching || !searchQuery.trim()}>{searching ? 'Searching…' : 'Search'}</button></form>
        <DocumentResultTable rows={searchResults} onSelect={(id) => { selectDocument(id); setView('documents') }} scores={searchScores} empty={!searching} />
      </section>}

      {view === 'compare' && <section className="tool-page"><div className="page-heading"><div><p className="eyebrow">CROSS-DOCUMENT CHECK</p><h1>Compare invoices</h1><p className="subheading">Choose two processed documents. The comparison uses stored extraction results.</p></div></div>
        <form className="tool-form compare-form" onSubmit={runComparison}><label>First document<select value={compareA} onChange={(e) => setCompareA(e.target.value)} required><option value="">Select document</option>{compareDocuments.map((doc) => <option value={doc.id} key={doc.id}>#{doc.id} · {doc.filename}</option>)}</select></label><label>Second document<select value={compareB} onChange={(e) => setCompareB(e.target.value)} required><option value="">Select document</option>{compareDocuments.map((doc) => <option value={doc.id} key={doc.id}>#{doc.id} · {doc.filename}</option>)}</select></label><button className="button button-primary" disabled={comparing || !compareA || !compareB || compareA === compareB}>{comparing ? 'Comparing…' : 'Compare selected'}</button></form>
        {comparison && <ComparisonReport report={comparison} />}
      </section>}
    </main>
    <footer className="footer"><span>Care Guide · Local document intelligence</span><span>Original documents remain unchanged</span></footer>
  </div>
}

function DocumentResultTable({ rows, onSelect, scores, empty }: { rows: DocumentSummary[]; onSelect: (id: number) => void; scores?: Record<number, number | null>; empty: boolean }) {
  if (!rows.length) return empty ? <div className="empty-state"><strong>No matching documents.</strong><span>Search results include stored document metadata only.</span></div> : null
  return <div className="table-wrap results-wrap"><table className="document-table"><thead><tr><th>Document</th><th>Invoice</th><th>Supplier</th><th>Status</th><th>Score</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><button className="document-link" onClick={() => onSelect(row.id)}>{row.filename}<small>Document #{row.id}</small></button></td><td>{row.invoice_number || '—'}</td><td>{row.supplier_name || '—'}</td><td><span className={`status-tag status-${row.status}`}>{row.status}</span></td><td>{scores?.[row.id] == null ? '—' : scores[row.id]?.toFixed(3)}</td></tr>)}</tbody></table></div>
}

function ComparisonReport({ report }: { report: CompareReport }) {
  const groups = [['Matched fields', report.matched_fields], ['Mismatched fields', report.mismatched_fields], ['Missing values', report.missing_fields]] as const
  return <section className="comparison-report" aria-label="Comparison results"><div className={`comparison-summary ${report.review_recommended ? 'needs-review' : ''}`}><strong>{report.comparable ? 'Documents compared' : 'Insufficient invoice identity evidence'}</strong><span>{report.review_recommended ? 'Human review recommended' : 'No review recommendation'}</span>{report.reasons.map((reason) => <small key={reason}>{reason}</small>)}</div>
    {groups.map(([title, rows]) => <div className="comparison-group" key={title}><h3>{title} <span>{rows.length}</span></h3>{rows.length ? <div className="table-wrap"><table className="document-table"><thead><tr><th>Field</th><th>Document A</th><th>Document B</th><th>Difference</th></tr></thead><tbody>{rows.map((item, index) => <tr key={`${String(item.field)}-${index}`}><td>{String(item.field ?? '—')}</td><td>{String(item.source_a ?? '—')}</td><td>{String(item.source_b ?? '—')}</td><td>{String(item.difference_a_minus_b ?? JSON.stringify(item.numeric_differences ?? '—'))}</td></tr>)}</tbody></table></div> : <p className="muted">None</p>}</div>)}
  </section>
}

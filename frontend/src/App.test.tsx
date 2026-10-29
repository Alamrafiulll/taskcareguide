import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const row = (overrides: Record<string, unknown> = {}) => ({
  id: 44, filename: 'therapy-invoice.pdf', status: 'completed', created_at: '2026-05-01T10:00:00Z', updated_at: '2026-05-01T10:00:00Z',
  invoice_number: 'CG-44', supplier_name: 'North Clinic', customer_name: 'Care Guide', invoice_date: '2026-05-01', currency: 'USD', validation_status: 'has_errors', validation_error_count: 1,
  error_details: null, job: { status: 'completed', retry_count: 0, attempt_count: 1, permanent_failure: false }, ...overrides,
})
const page = (items: ReturnType<typeof row>[]) => ({ total: items.length, page: 1, page_size: 10, items })
const invoice = {
  id: 7, document_id: 44, vendor_name: 'North Clinic', customer_name: 'Care Guide', invoice_number: 'CG-44', invoice_date: '2026-05-01', due_date: null, currency: 'USD',
  subtotal: '100.00', tax_amount: '20.00', total_amount: '125.00', extracted_text_path: null,
  raw_extraction: { invoice: { invoice_number: 'CG-44', invoice_date: '2026-05-01', supplier_name: 'North Clinic', customer_name: 'Care Guide', currency: 'USD', subtotal: '100.00', tax_amount: '20.00', total_amount: '125.00', field_status: { tax_amount: 'extracted' }, warnings: [{ code: 'field_uncertain', field: 'invoice_date', message: 'Verify date against the source.' }] } },
  extraction_method: 'deterministic', field_confidence: { invoice_number: 0.9, invoice_date: 0.6 }, created_at: '2026-05-01T10:00:00Z', updated_at: '2026-05-01T10:00:00Z',
  line_items: [{ id: 2, description: 'Consultation', quantity: '2', unit_price: '50.00', amount: '100.00', position: 0 }],
  validation_errors: [{ id: 3, field_name: 'total_amount', code: 'subtotal_tax_total_mismatch', severity: 'error', message: 'Subtotal plus tax does not reconcile.', explanation: '100 + 20 differs from 125.' }],
}
const history = [{ id: 1, event_type: 'processing_completed', status: 'completed', details: 'Invoice extraction completed.', event_data: null, created_at: '2026-05-01T10:00:00Z' }]

function mockApi(options: { uploadError?: boolean; localAI?: boolean; documents?: ReturnType<typeof row>[] } = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    if (url.endsWith('/api/ready')) return Response.json({ status: 'ready', checks: { database: 'ok', worker: 'ok', ocr: 'optional_not_installed', local_ai: options.localAI ? 'enabled' : 'disabled' } })
    if (url.includes('/api/documents/upload') && init?.method === 'POST') {
      if (options.uploadError) return Response.json({ error: { code: 'invalid_signature', message: 'The selected PDF is not a valid PDF file.' } }, { status: 422 })
      options.documents = [row({ filename: 'new-invoice.csv', id: 45, status: 'queued', job: { status: 'queued', retry_count: 0, attempt_count: 0, permanent_failure: false } })]
      return Response.json({ accepted_count: 1, rejected_count: 0, items: [{ document_id: 45, filename: 'new-invoice.csv', status: 'queued' }] }, { status: 202 })
    }
    if (url.includes('/api/documents?')) return Response.json(page(options.documents ?? []))
    if (url.endsWith('/api/documents/44/invoice') && init?.method === 'PUT') return Response.json(invoice)
    if (url.endsWith('/api/documents/44/invoice')) return Response.json(invoice)
    if (url.endsWith('/api/documents/44/invoice/ai-suggestions') && init?.method === 'POST') return Response.json({
      document_id: 44, status: 'suggestions_ready', model: 'local-test-model', suggestions: [
        { field: 'total_amount', current_value: '125.00', suggested_value: '120.00', evidence: ['Total: 120.00'] },
      ],
    })
    if (url.endsWith('/api/documents/44/history')) return Response.json(history)
    if (url.endsWith('/api/documents/44/validation-errors')) return Response.json(invoice.validation_errors.map(({ field_name, ...item }) => ({ ...item, field: field_name })))
    if (url.endsWith('/api/documents/44/invoice/corrections')) return Response.json([])
    if (url.endsWith('/api/documents/44')) return Response.json(row())
    if (url.endsWith('/api/documents/45')) return Response.json(row({ id: 45, filename: 'new-invoice.csv', status: 'queued' }))
    if (url.endsWith('/api/documents/45/history') || url.endsWith('/api/documents/45/validation-errors') || url.endsWith('/api/documents/45/invoice/corrections')) return Response.json([])
    if (url.endsWith('/api/documents/45/invoice')) return Response.json({ error: { code: 'invoice_not_found', message: 'Not processed.' } }, { status: 404 })
    return Response.json({ error: { code: 'not_found', message: 'No matching mock route.' } }, { status: 404 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

describe('document workspace', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()) })

  it('queues a selected file through the multi-file upload endpoint and renders the result', async () => {
    const { calls } = mockApi()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('No documents match these filters.')
    const file = new File(['Invoice Number,CG-45'], 'new-invoice.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText('Choose invoice files'), { target: { files: [file] } })
    await user.click(screen.getByRole('button', { name: 'Upload and queue' }))
    expect(await screen.findByText('Queued · document 45')).toBeTruthy()
    const upload = calls.find((call) => call.url.includes('/api/documents/upload'))
    expect(upload?.init?.method).toBe('POST')
    expect(upload?.init?.body).toBeInstanceOf(FormData)
    expect((await screen.findAllByText('new-invoice.csv')).length).toBeGreaterThan(0)
  })

  it('shows validation feedback returned by the backend', async () => {
    mockApi({ uploadError: true })
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('No documents match these filters.')
    const file = new File(['not pdf'], 'broken.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText('Choose invoice files'), { target: { files: [file] } })
    await user.click(screen.getByRole('button', { name: 'Upload and queue' }))
    expect((await screen.findByRole('alert')).textContent).toContain('The selected PDF is not a valid PDF file.')
  })

  it('renders stored invoice fields, validation warnings, line items, and audit history', async () => {
    mockApi({ documents: [row()] })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: /therapy-invoice.pdf/ }))
    expect(await screen.findByRole('heading', { name: 'Invoice details' })).toBeTruthy()
    expect(screen.getByLabelText('Invoice number')).toHaveProperty('value', 'CG-44')
    expect(screen.getByLabelText('Total amount')).toHaveProperty('value', '125.00')
    expect(screen.getByText('Subtotal plus tax does not reconcile.')).toBeTruthy()
    expect(screen.getByText('Verify date against the source.')).toBeTruthy()
    expect(screen.getByLabelText('Line 1 description')).toHaveProperty('value', 'Consultation')
    expect(screen.getByText('processing completed')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Download original JSON' })).toHaveProperty('href', expect.stringContaining('view=original'))
    expect(screen.getByRole('link', { name: 'Download current JSON' })).toHaveProperty('href', expect.stringContaining('view=corrected'))
  })

  it('shows local model proposals, stages reviewer-selected values, and saves them with model provenance', async () => {
    const { calls } = mockApi({ localAI: true, documents: [row()] })
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: /therapy-invoice.pdf/ }))
    await user.click(await screen.findByRole('button', { name: 'Suggest corrections with Ollama' }))
    expect(await screen.findByText('Total: 120.00')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Use selected suggestions in form' }))
    expect(screen.getByLabelText('Total amount')).toHaveProperty('value', '120.00')
    await user.click(screen.getByRole('button', { name: 'Save corrections' }))
    const save = calls.find((call) => call.url.endsWith('/api/documents/44/invoice') && call.init?.method === 'PUT')
    expect(JSON.parse(String(save?.init?.body)).correction_source).toBe('local_model')
    expect(JSON.parse(String(save?.init?.body)).correction_model).toBe('local-test-model')
    expect(await screen.findByText('Correction saved. Validation was rerun and the audit history was updated.')).toBeTruthy()
  })

  it('prevents unsupported files before sending them to the API', async () => {
    const { calls } = mockApi()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByText('No documents match these filters.')
    const file = new File(['bad'], 'malware.exe', { type: 'application/octet-stream' })
    fireEvent.change(screen.getByLabelText('Choose invoice files'), { target: { files: [file] } })
    expect(await screen.findByText('Choose a PDF, JPG, PNG, TIFF, XLS, XLSX, or CSV file.')).toBeTruthy()
    expect(calls.some((call) => call.url.includes('/api/documents/upload'))).toBe(false)
  })
})

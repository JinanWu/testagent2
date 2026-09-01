import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiFormatError,
} from '../api/client'
import {
  RedactionMutationError,
  normalizeRedactionReason,
  redactInvocation,
  type AdminInvocationDetail,
} from '../api/logs'
import AdminInvocationDetailView from '../features/logs/AdminInvocationDetail'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function receipt(): Record<string, unknown> {
  return {
    redaction_id: 'redaction-1', invocation_id: 'invocation-1', target_type: 'metadata',
    target_row_id: 'invocation-1', json_path: '/secret', original_sha256: 'a'.repeat(64),
    reason: 'privacy request', actor: { type: 'admin', id: 'admin-1' },
    audit_event_id: 'audit-1', is_tombstone: true, redacted_at: 10,
  }
}

function detail(): AdminInvocationDetail {
  return {
    invocation: { id: 'invocation-1', requestId: 'request-1', sessionId: null },
    endpointId: 'endpoint-1', endpointVersionId: 'version-1', credentialId: null,
    messageId: null, status: 'failed', input: null, metadata: null, output: null,
    error: null, usage: null, metadataSizeBytes: null, metadataSha256: null,
    latencyMs: null, pricingVersion: null, createdAt: 1, completedAt: 2,
    runEvents: [], toolCalls: [], redactions: [], sensitiveHits: [],
  }
}

describe('A20不可逆遮蔽client契約', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('只trim backend列出的29種碼點且保留FEFF', () => {
    expect(normalizeRedactionReason('\u3000 privacy request\u0085')).toBe('privacy request')
    expect(normalizeRedactionReason('\ufeffprivacy request')).toBe('\ufeffprivacy request')
    expect(() => normalizeRedactionReason('\ud800')).toThrow(ApiFormatError)
    expect(() => normalizeRedactionReason('Bearer secret')).toThrow(ApiFormatError)
  })

  it('發送exact same-origin credentialed POST並strict decode safe receipt', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(receipt()))
    await expect(redactInvocation(
      'endpoint-1', 'invocation-1', {
        targetType: 'metadata', targetRowId: 'invocation-1', jsonPath: '/secret',
        reason: '\u3000privacy request ',
      },
      'idempotency-1', 'csrf-safe',
    )).resolves.toMatchObject({
      id: 'redaction-1', invocationId: 'invocation-1', isTombstone: true,
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/published-endpoints/endpoint-1/invocations/invocation-1/redactions',
      expect.objectContaining({
        method: 'POST', credentials: 'include',
        headers: {
          Accept: 'application/json', 'Content-Type': 'application/json',
          'X-CSRF-Token': 'csrf-safe', 'Idempotency-Key': 'idempotency-1',
        },
        body: JSON.stringify({
          target_type: 'metadata', target_row_id: 'invocation-1',
          json_path: '/secret', reason: 'privacy request',
        }),
      }),
    )
  })

  it('只接受status與canonical error code配對', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { code: 'redaction_conflict' } }, 409))
    await expect(redactInvocation(
      'endpoint-1', 'invocation-1', {
        targetType: 'metadata', targetRowId: 'invocation-1', jsonPath: '', reason: 'privacy',
      }, 'idempotency-1', 'csrf-safe',
    )).rejects.toEqual(expect.objectContaining<Partial<RedactionMutationError>>({
      status: 409, code: 'redaction_conflict',
    }))
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { code: 'unauthorized' } }, 409))
    await expect(redactInvocation(
      'endpoint-1', 'invocation-1', {
        targetType: 'metadata', targetRowId: 'invocation-1', jsonPath: '', reason: 'privacy',
      }, 'idempotency-2', 'csrf-safe',
    )).rejects.toBeInstanceOf(ApiFormatError)
  })
})

describe('A20不可逆遮蔽二次確認UI', () => {
  let renderer: ReactTestRenderer | undefined
  afterEach(async () => {
    if (renderer) await act(async () => renderer!.unmount())
    renderer = undefined
  })

  it('首次submit只開確認dialog，確認按鈕才送一次', async () => {
    const onRedact = vi.fn(async (): Promise<void> => undefined)
    await act(async () => {
      renderer = create(<AdminInvocationDetailView detail={detail()} hasRedaction={false}
        redactionPending={false} onRedact={onRedact} />)
    })
    const reason = renderer!.root.findByProps({ id: 'redaction-reason' })
    await act(async () => reason.props.onChange({ currentTarget: { value: 'privacy request' } }))
    await act(async () => renderer!.root.findByProps({ 'aria-label': '建立不可逆遮蔽' }).props.onSubmit({
      preventDefault: vi.fn(),
    }))
    expect(onRedact).not.toHaveBeenCalled()
    expect(renderer!.root.findByProps({ role: 'dialog' })).toBeDefined()
    const confirm = renderer!.root.findAllByType('button').find(
      (button) => button.children.join('') === '確認永久遮蔽',
    )!
    await act(async () => confirm.props.onClick())
    expect(onRedact).toHaveBeenCalledTimes(1)
    expect(onRedact).toHaveBeenCalledWith(expect.objectContaining({
      targetType: 'metadata', targetRowId: 'invocation-1', reason: 'privacy request',
    }))
  })
})

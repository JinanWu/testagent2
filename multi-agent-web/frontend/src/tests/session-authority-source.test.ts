import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  consumeProtectedOperation,
  createSendChatOperation,
  createUploadImageOperation,
} from '../app/sessionAuthority'

describe('session authority sealed operation boundary', () => {
  it('consumes each opaque operation exactly once and rejects forged values', () => {
    const operation = createSendChatOperation('  hello  ', 'session-1')
    expect(consumeProtectedOperation(operation)).toEqual({
      kind: 'send-chat', message: 'hello', sessionId: 'session-1', images: [],
    })
    expect(() => consumeProtectedOperation(operation)).toThrowError(/取消/)
    expect(() => consumeProtectedOperation({} as never)).toThrowError(/取消/)
  })

  it('carries uploaded image refs on the sealed chat manifest', () => {
    const 參照 = 'gs://b/images/u/0123456789abcdef0123456789abcdef.png'
    expect(consumeProtectedOperation(createSendChatOperation('hi', null, [參照]))).toEqual({
      kind: 'send-chat', message: 'hi', sessionId: null, images: [參照],
    })
    /* 超過上限就不該產生 token——避免多餘的圖片一路送到後端才被拒 */
    expect(() => createSendChatOperation('hi', null, Array(5).fill(參照))).toThrowError(/取消/)
  })

  it('rejects empty or oversized image uploads before they reach the network', () => {
    const 圖片 = new Blob([new Uint8Array(16)], { type: 'image/png' })
    const 操作 = createUploadImageOperation(圖片)
    expect(consumeProtectedOperation(操作)).toEqual({ kind: 'upload-image', file: 圖片 })
    expect(() => createUploadImageOperation(new Blob([]))).toThrowError(/取消/)
    expect(() => createUploadImageOperation(
      new Blob([new Uint8Array(20 * 1024 * 1024 + 1)]),
    )).toThrowError(/取消/)
  })

  it('keeps csrf and unsafe replacement out of the public Session Context', () => {
    const source = readFileSync(new URL('../app/SessionProvider.tsx', import.meta.url), 'utf8')
    const context = source.slice(source.indexOf('export interface SessionContextValue'), source.indexOf('const SessionContext'))
    expect(context).not.toContain('replaceSession')
    expect(context).not.toContain('csrf')
    expect(source).not.toMatch(/status: 'authenticated'; session:/)
  })
})

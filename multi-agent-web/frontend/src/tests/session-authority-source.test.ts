import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import {
  consumeProtectedOperation,
  createSendChatOperation,
} from '../app/sessionAuthority'

describe('session authority sealed operation boundary', () => {
  it('consumes each opaque operation exactly once and rejects forged values', () => {
    const operation = createSendChatOperation('  hello  ', 'session-1')
    expect(consumeProtectedOperation(operation)).toEqual({
      kind: 'send-chat', message: 'hello', sessionId: 'session-1',
    })
    expect(() => consumeProtectedOperation(operation)).toThrowError(/取消/)
    expect(() => consumeProtectedOperation({} as never)).toThrowError(/取消/)
  })

  it('keeps csrf and unsafe replacement out of the public Session Context', () => {
    const source = readFileSync(new URL('../app/SessionProvider.tsx', import.meta.url), 'utf8')
    const context = source.slice(source.indexOf('export interface SessionContextValue'), source.indexOf('const SessionContext'))
    expect(context).not.toContain('replaceSession')
    expect(context).not.toContain('csrf')
    expect(source).not.toMatch(/status: 'authenticated'; session:/)
  })
})

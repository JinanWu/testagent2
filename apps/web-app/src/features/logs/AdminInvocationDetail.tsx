import type { AdminInvocationDetail, JsonValue } from '../../api/logs'

function isCanonicalTombstone(value: JsonValue): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const keys = Object.keys(value)
  if (keys.length !== 1 || keys[0] !== '$tombstone') return false
  const tombstone = value.$tombstone
  return typeof tombstone === 'object' && tombstone !== null && !Array.isArray(tombstone) &&
    Object.keys(tombstone).length === 2 && typeof tombstone.redaction_id === 'string' &&
    typeof tombstone.redacted_at === 'number'
}

function sanitizeTombstones(value: JsonValue): JsonValue {
  if (isCanonicalTombstone(value)) return '已遮蔽'
  if (Array.isArray(value)) return value.map(sanitizeTombstones)
  if (typeof value !== 'object' || value === null) return value
  return Object.fromEntries(Object.entries(value).map(
    ([key, child]) => [key, sanitizeTombstones(child)],
  ))
}

function renderValue(value: JsonValue): string {
  if (value === null) return '無資料'
  return JSON.stringify(sanitizeTombstones(value), null, 2)
}

function renderEpoch(value: number): string {
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? date.toISOString() : '時間不可顯示'
}

function RawField({ label, value }: { label: string; value: JsonValue }) {
  return (
    <section aria-label={label}>
      <h3>{label}</h3>
      {value === null
        ? <p>無資料</p>
        : isCanonicalTombstone(value) ? <p>已遮蔽</p> : <pre>{renderValue(value)}</pre>}
    </section>
  )
}

export interface AdminInvocationDetailProps {
  detail: AdminInvocationDetail
  hasRedaction: boolean
}

export default function AdminInvocationDetail({ detail, hasRedaction }: AdminInvocationDetailProps) {
  const containsRedaction = hasRedaction || detail.redactions.length > 0
  const targetLabels: Record<string, string> = {
    invocation_input: '輸入', metadata: 'Metadata', output: '輸出', error: '錯誤',
    run_event: '執行事件', tool_arguments: '工具參數', tool_result: '工具結果',
    tool_error: '工具錯誤',
  }
  return (
    <article aria-labelledby="invocation-detail-title">
      <h2 id="invocation-detail-title">呼叫詳情</h2>
      {containsRedaction && <p role="status">部分內容已依政策遮蔽，無法還原原文。</p>}
      <dl>
        <dt>呼叫識別碼</dt><dd>{detail.invocation.id}</dd>
        <dt>狀態</dt><dd>{detail.status}</dd>
        <dt>建立時間</dt><dd>{renderEpoch(detail.createdAt)}</dd>
        <dt>延遲</dt><dd>{detail.latencyMs === null ? '無資料' : `${detail.latencyMs} ms`}</dd>
      </dl>
      <RawField label="輸入" value={detail.input} />
      <RawField label="Metadata" value={detail.metadata} />
      <RawField label="輸出" value={detail.output} />
      <RawField label="錯誤" value={detail.error} />
      <RawField label="Usage" value={detail.usage} />
      <section aria-label="執行事件">
        <h3>執行事件</h3>
        {detail.runEvents.length === 0 ? <p>沒有執行事件。</p> : detail.runEvents.map((event) => (
          <article key={event.id}>
            <h4>{event.eventType}</h4>
            <pre>{renderValue(event.payload)}</pre>
          </article>
        ))}
      </section>
      <section aria-label="工具呼叫">
        <h3>工具呼叫</h3>
        {detail.toolCalls.length === 0 ? <p>沒有工具呼叫。</p> : detail.toolCalls.map((tool) => (
          <article key={tool.id}>
            <h4>{tool.toolName} — {tool.outcome}</h4>
            <RawField label={`${tool.toolName} 參數`} value={tool.arguments} />
            <RawField label={`${tool.toolName} 結果`} value={tool.result} />
            <RawField label={`${tool.toolName} 錯誤`} value={tool.error} />
          </article>
        ))}
      </section>
      <section aria-label="遮蔽紀錄">
        <h3>遮蔽紀錄</h3>
        {detail.redactions.length === 0 ? <p>沒有遮蔽紀錄。</p> : (
          <ul>{detail.redactions.map((redaction) => (
            <li key={redaction.id}>
              <strong>{targetLabels[redaction.targetType]}</strong>
              <span>路徑：{redaction.jsonPath === '' ? '根節點' : redaction.jsonPath}</span>
              <span>原因：{redaction.reason}</span>
              <time dateTime={renderEpoch(redaction.redactedAt)}>{renderEpoch(redaction.redactedAt)}</time>
            </li>
          ))}</ul>
        )}
      </section>
    </article>
  )
}

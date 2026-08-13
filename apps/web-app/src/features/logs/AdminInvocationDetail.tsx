import type { AdminInvocationDetail, JsonValue } from '../../api/logs'

function renderValue(value: JsonValue): string {
  if (value === null) return '已刪除或無資料'
  return JSON.stringify(value, null, 2)
}

function renderEpoch(value: number): string {
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? date.toISOString() : '時間不可顯示'
}

function RawField({ label, value }: { label: string; value: JsonValue }) {
  return (
    <section aria-label={label}>
      <h3>{label}</h3>
      {value === null ? <p>已刪除或無資料</p> : <pre>{renderValue(value)}</pre>}
    </section>
  )
}

export interface AdminInvocationDetailProps {
  detail: AdminInvocationDetail
  hasRedaction: boolean
}

export default function AdminInvocationDetail({ detail, hasRedaction }: AdminInvocationDetailProps) {
  return (
    <article aria-labelledby="invocation-detail-title">
      <h2 id="invocation-detail-title">呼叫詳情</h2>
      {hasRedaction && <p role="status">部分內容已依政策遮蔽，無法還原原文。</p>}
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
    </article>
  )
}

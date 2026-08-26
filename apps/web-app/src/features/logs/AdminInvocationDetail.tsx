import { useState, type FormEvent, type ReactNode } from 'react'
import type { AdminInvocationDetail, JsonValue, RedactionRequest } from '../../api/logs'
import { 卡片, 空狀態, 欄位, 狀態色調, 狀態標籤, 資料列, 輸入樣式, 等寬輸入樣式 } from '../../ui/元件'
import { 狀態文字 } from '../../ui/格式'
import 圖示 from '../../ui/圖示'

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
    <section aria-label={label} className="flex flex-col gap-xs">
      <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
        {label}
      </h3>
      {value === null ? (
        <p className="font-body-md text-body-md text-on-surface-variant">無資料</p>
      ) : isCanonicalTombstone(value) ? (
        <p className="flex items-center gap-sm rounded border border-outline-variant bg-surface-container px-md py-sm font-body-md text-body-md text-on-surface-variant">
          <span aria-hidden={true}><圖示 名稱="隱藏" 大小={16} /></span>
          已遮蔽
        </p>
      ) : (
        /* 稽核內容刻意不提供複製／匯出入口 */
        <pre className="程式碼區塊 max-h-72">{renderValue(value)}</pre>
      )}
    </section>
  )
}

/*
 * usage 是後端原樣回傳的 JSON，欄位視 provider 而定，
 * 因此已知的 token 欄位用具名列呈現，取不到就顯示「無資料」，原文一律留在「Usage」原始資料區塊。
 */
function 用量數字(usage: JsonValue, 候選鍵: readonly string[]): string {
  if (typeof usage !== 'object' || usage === null || Array.isArray(usage)) return '無資料'
  for (const 鍵 of 候選鍵) {
    const 值 = (usage as Record<string, JsonValue>)[鍵]
    if (typeof 值 === 'number' && Number.isFinite(值)) return 值.toLocaleString('en-US')
  }
  return '無資料'
}

function 位元組(值: number | null): string {
  return 值 === null ? '無資料' : `${值.toLocaleString('en-US')} B`
}

function 代碼(值: string | null): ReactNode {
  return 值 === null
    ? <span className="text-on-surface-variant">無資料</span>
    : <code className="font-code-md text-code-md">{值}</code>
}

export interface AdminInvocationDetailProps {
  detail: AdminInvocationDetail
  hasRedaction: boolean
  redactionPending: boolean
  onRedact(request: RedactionRequest): Promise<void>
}

export default function AdminInvocationDetail({
  detail, hasRedaction, redactionPending, onRedact,
}: AdminInvocationDetailProps) {
  const [targetType, setTargetType] = useState('metadata')
  const [targetRowId, setTargetRowId] = useState(detail.invocation.id)
  const [jsonPath, setJsonPath] = useState('')
  const [reason, setReason] = useState('')
  const [confirmation, setConfirmation] = useState<RedactionRequest | null>(null)
  function prepareRedaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setConfirmation({ targetType, targetRowId, jsonPath, reason })
  }
  const containsRedaction = hasRedaction || detail.redactions.length > 0
  const targetLabels: Record<string, string> = {
    invocation_input: '輸入', metadata: 'Metadata', output: '輸出', error: '錯誤',
    run_event: '執行事件', tool_arguments: '工具參數', tool_result: '工具結果',
    tool_error: '工具錯誤',
  }
  const sensitiveTargetLabels: Record<string, string> = {
    input: '輸入', metadata: 'Metadata', response_data: '回應資料',
    tool_arguments: '工具參數', tool_result: '工具結果',
  }
  /*
   * 五個原始資料區塊一律同時可見，不做 tab 切換：
   * 稽核情境要能一眼並排比對輸入與輸出，A18 browser smoke 也直接斷言兩者同時 visible。
   * 版面改用雙欄網格收斂高度，取代原本五段直落的長 pre。
   */
  const rawSections: readonly { 名稱: string; 值: JsonValue }[] = [
    { 名稱: '輸入', 值: detail.input },
    { 名稱: 'Metadata', 值: detail.metadata },
    { 名稱: '輸出', 值: detail.output },
    { 名稱: '錯誤', 值: detail.error },
    { 名稱: 'Usage', 值: detail.usage },
  ]

  return (
    <article aria-labelledby="invocation-detail-title" className="flex flex-col gap-md">
      <h2 id="invocation-detail-title" className="sr-only">呼叫詳情</h2>

      <div className="flex flex-wrap items-start justify-between gap-sm">
        <div className="min-w-0">
          <p className="truncate font-code-md text-headline-sm text-on-surface">
            {detail.invocation.requestId}
          </p>
          <p className="font-body-md text-body-md text-on-surface-variant">
            {`端點 ${detail.endpointId}・版本 ${detail.endpointVersionId}・`}
            <time dateTime={renderEpoch(detail.createdAt)}>{renderEpoch(detail.createdAt)}</time>
          </p>
        </div>
        <狀態標籤 色調={狀態色調(detail.status)}>{狀態文字(detail.status)}</狀態標籤>
      </div>

      {containsRedaction && (
        <p role="status"
          className="flex items-center gap-sm rounded border border-outline-variant bg-surface-container px-md py-sm font-body-md text-body-md text-on-surface-variant">
          <span aria-hidden={true}><圖示 名稱="隱藏" 大小={16} /></span>
          部分內容已依政策遮蔽，無法還原原文。
        </p>
      )}

      <div className="grid gap-md lg:grid-cols-2">
        <卡片 標題="基本資訊">
          <dl>
            <資料列 名稱="狀態"><code className="font-code-md text-code-md">{detail.status}</code></資料列>
            <資料列 名稱="Request ID">{代碼(detail.invocation.requestId)}</資料列>
            <資料列 名稱="呼叫識別碼">{代碼(detail.invocation.id)}</資料列>
            <資料列 名稱="Session ID">{代碼(detail.invocation.sessionId)}</資料列>
            <資料列 名稱="Message ID">{代碼(detail.messageId)}</資料列>
            <資料列 名稱="Credential ID">{代碼(detail.credentialId)}</資料列>
            <資料列 名稱="端點版本">{代碼(detail.endpointVersionId)}</資料列>
            <資料列 名稱="建立時間">{renderEpoch(detail.createdAt)}</資料列>
            <資料列 名稱="完成時間">
              {detail.completedAt === null ? '無資料' : renderEpoch(detail.completedAt)}
            </資料列>
            <資料列 名稱="延遲">
              {detail.latencyMs === null ? '無資料' : `${detail.latencyMs} ms`}
            </資料列>
          </dl>
        </卡片>

        <卡片 標題="用量與計費">
          <dl>
            <資料列 名稱="Prompt Tokens">{用量數字(detail.usage, ['prompt_tokens', 'input_tokens'])}</資料列>
            <資料列 名稱="Completion Tokens">{用量數字(detail.usage, ['completion_tokens', 'output_tokens'])}</資料列>
            <資料列 名稱="Total Tokens">{用量數字(detail.usage, ['total_tokens'])}</資料列>
            <資料列 名稱="Pricing 版本">{代碼(detail.pricingVersion)}</資料列>
            <資料列 名稱="Metadata 大小">{位元組(detail.metadataSizeBytes)}</資料列>
            <資料列 名稱="Metadata SHA-256">{代碼(detail.metadataSha256)}</資料列>
          </dl>
        </卡片>
      </div>

      <卡片 標題="原始資料">
        <div className="grid gap-md lg:grid-cols-2">
          {rawSections.map((區塊) => (
            <RawField key={區塊.名稱} label={區塊.名稱} value={區塊.值} />
          ))}
        </div>
      </卡片>

      <卡片 標題="敏感資料命中">
        <section aria-label="敏感資料命中">
          <h3 className="sr-only">敏感資料命中</h3>
          {detail.sensitiveHits.length === 0 ? <空狀態>沒有敏感資料命中。</空狀態> : (
            <div className="overflow-x-auto">
              <table className="w-full text-left font-body-md text-body-md">
                <thead>
                  <tr className="border-b border-outline-variant text-on-surface-variant">
                    <th scope="col" className="py-2 pr-md font-semibold">偵測類型</th>
                    <th scope="col" className="py-2 pr-md font-semibold">目標</th>
                    <th scope="col" className="py-2 pr-md font-semibold">工具呼叫識別碼</th>
                    <th scope="col" className="py-2 pr-md font-semibold">JSON 路徑</th>
                    <th scope="col" className="py-2 pr-md font-semibold">位置</th>
                    <th scope="col" className="py-2 font-semibold">偵測時間</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.sensitiveHits.map((hit) => (
                    <tr key={hit.id} className="border-b border-outline-variant/40 last:border-b-0">
                      <td className="py-2 pr-md font-code-md text-code-md text-error">{hit.detectorType}</td>
                      <td className="py-2 pr-md text-on-surface-variant">{sensitiveTargetLabels[hit.target]}</td>
                      <td className="py-2 pr-md font-code-md text-code-md text-on-surface-variant">
                        {hit.toolCallId ?? '無資料'}
                      </td>
                      <td className="py-2 pr-md font-code-md text-code-md text-on-surface-variant">
                        {hit.jsonPath === '' ? '根節點' : hit.jsonPath}
                      </td>
                      <td className="py-2 pr-md font-code-md text-code-md text-on-surface-variant">
                        {`${hit.start}–${hit.end}`}
                      </td>
                      <td className="py-2 font-code-md text-code-md text-on-surface-variant">
                        <time dateTime={renderEpoch(hit.detectedAt)}>{renderEpoch(hit.detectedAt)}</time>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </卡片>

      <div className="grid gap-md xl:grid-cols-2">
        <卡片 標題="工具呼叫">
          <section aria-label="工具呼叫" className="flex flex-col gap-md">
            <h3 className="sr-only">工具呼叫</h3>
            {detail.toolCalls.length === 0 ? <空狀態>沒有工具呼叫。</空狀態> : detail.toolCalls.map((tool) => (
              <article key={tool.id}
                className="flex flex-col gap-sm rounded-lg border border-outline-variant bg-surface-container-low p-md">
                <h4 className="flex flex-wrap items-center justify-between gap-sm">
                  <span className="font-code-md text-code-md font-semibold text-on-surface">
                    {`${tool.toolName} — ${tool.outcome}`}
                  </span>
                  <span className="font-code-md text-label-sm text-on-surface-variant">
                    {`#${tool.sequenceNumber}・${tool.latencyMs === null ? '延遲無資料' : `${tool.latencyMs} ms`}`}
                  </span>
                </h4>
                {tool.retryOfToolCallId !== null && (
                  <p className="font-body-md text-body-md text-on-surface-variant">
                    {`重試自 ${tool.retryOfToolCallId}`}
                  </p>
                )}
                <RawField label={`${tool.toolName} 參數`} value={tool.arguments} />
                <RawField label={`${tool.toolName} 結果`} value={tool.result} />
                <RawField label={`${tool.toolName} 錯誤`} value={tool.error} />
              </article>
            ))}
          </section>
        </卡片>

        <卡片 標題="執行事件">
          <section aria-label="執行事件" className="flex flex-col gap-md">
            <h3 className="sr-only">執行事件</h3>
            {detail.runEvents.length === 0 ? <空狀態>沒有執行事件。</空狀態> : detail.runEvents.map((event) => (
              <article key={event.id} className="flex flex-col gap-xs">
                <h4 className="flex flex-wrap items-baseline justify-between gap-sm">
                  <span className="font-code-md text-code-md font-semibold text-on-surface">
                    {`#${event.sequenceNumber} ${event.eventType}`}
                  </span>
                  <time dateTime={renderEpoch(event.createdAt)}
                    className="font-code-md text-label-sm text-on-surface-variant">
                    {renderEpoch(event.createdAt)}
                  </time>
                </h4>
                <pre className="程式碼區塊 max-h-72">{renderValue(event.payload)}</pre>
              </article>
            ))}
          </section>
        </卡片>
      </div>

      {/*
        遮蔽紀錄。
        設計稿右下那張「管理稽核（Detail View Accessed / Redaction Executed）」做不到：
        操作軌跡在 audit_events 表，但後端沒有查詢 API（只寫不讀）。
        遮蔽這一半的執行者資訊倒是拿得到（actor.id ＋ auditEventId），所以併進這張卡。
      */}
      <卡片 標題="遮蔽紀錄">
        <section aria-label="遮蔽紀錄">
          <h3 className="sr-only">遮蔽紀錄</h3>
          {detail.redactions.length === 0 ? <空狀態>沒有遮蔽紀錄。</空狀態> : (
            <ul className="flex flex-col gap-sm">
              {detail.redactions.map((redaction) => (
                <li key={redaction.id}
                  className="flex flex-col gap-xs rounded border border-outline-variant bg-surface-container-low p-md font-body-md text-body-md">
                  <div className="flex flex-wrap items-center justify-between gap-sm">
                    <strong className="flex items-center gap-sm text-on-surface">
                      <span aria-hidden={true} className="text-error"><圖示 名稱="隱藏" 大小={14} /></span>
                      {targetLabels[redaction.targetType]}
                    </strong>
                    <time dateTime={renderEpoch(redaction.redactedAt)}
                      className="font-code-md text-label-sm text-on-surface-variant">
                      {renderEpoch(redaction.redactedAt)}
                    </time>
                  </div>
                  <span className="text-on-surface-variant">
                    路徑：{redaction.jsonPath === '' ? '根節點' : redaction.jsonPath}
                  </span>
                  <span className="text-on-surface-variant">原因：{redaction.reason}</span>
                  <span className="text-on-surface-variant">
                    {`執行者：${redaction.actor.id}・稽核事件 ${redaction.auditEventId}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </卡片>

      {/*
        危險區：唯一會永久破壞資料的操作，視覺語言與其他區塊刻意區隔，
        且固定放在整頁最底（設計需求 §9：不能讓它容易被順手點到），
        所以詳情標頭刻意不放遮蔽按鈕。
      */}
      <section aria-label="不可逆遮蔽"
        className="mt-md overflow-hidden rounded-lg border-2 border-error/50 bg-error/5">
        <div className="flex items-start gap-sm border-b border-error/20 bg-error-container px-md py-sm text-on-error-container">
          <span aria-hidden={true} className="mt-0.5 shrink-0"><圖示 名稱="危險" 大小={20} /></span>
          <div>
            <h3 className="font-headline-sm text-headline-sm">不可逆遮蔽</h3>
            <p className="font-body-md text-body-md">
              此操作會永久以tombstone取代指定內容，無法還原。
            </p>
          </div>
        </div>
        <form aria-label="建立不可逆遮蔽" onSubmit={prepareRedaction}
          className="grid gap-md p-md sm:grid-cols-2">
          <欄位 標籤="目標類型" htmlFor="redaction-target-type">
            <select id="redaction-target-type" value={targetType} className={輸入樣式}
              onChange={(event) => setTargetType(event.currentTarget.value)}>
              {Object.entries(targetLabels).map(([value, label]) =>
                <option key={value} value={value}>{label}</option>)}
            </select>
          </欄位>
          <欄位 標籤="目標資料列識別碼" htmlFor="redaction-target-row">
            <input id="redaction-target-row" required maxLength={128} value={targetRowId} className={等寬輸入樣式}
              onChange={(event) => setTargetRowId(event.currentTarget.value)} />
          </欄位>
          <欄位 標籤="JSON Pointer" htmlFor="redaction-json-path"
            提示="空白代表整份文件。一次只能遮蔽一個目標。" className="sm:col-span-2">
            <input id="redaction-json-path" maxLength={4096} value={jsonPath} className={等寬輸入樣式}
              onChange={(event) => setJsonPath(event.currentTarget.value)} />
          </欄位>
          <欄位 標籤="遮蔽原因" htmlFor="redaction-reason" className="sm:col-span-2">
            <textarea id="redaction-reason" required maxLength={256} value={reason} rows={2}
              className={`${輸入樣式} resize-y`}
              onChange={(event) => setReason(event.currentTarget.value)} />
          </欄位>
          <div className="sm:col-span-2">
            <button type="submit" disabled={redactionPending}
              className="rounded border border-error bg-error px-4 py-2 font-body-md text-body-md font-semibold text-on-error transition-colors hover:bg-error/90 disabled:cursor-not-allowed disabled:opacity-50">
              準備不可逆遮蔽
            </button>
          </div>
        </form>

        {confirmation && (
          <div role="dialog" aria-modal="true" aria-labelledby="redaction-confirm-title"
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-md backdrop-blur-sm">
            <div className="w-full max-w-[30rem] overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest shadow-xl">
              <div className="flex items-start gap-md border-b border-error/20 bg-error-container p-lg">
                <span aria-hidden={true} className="shrink-0 text-error"><圖示 名稱="危險" 大小={28} /></span>
                <div>
                  <h4 id="redaction-confirm-title" className="font-headline-sm text-headline-sm text-on-error-container">
                    確認永久遮蔽
                  </h4>
                  <p className="mt-2 font-body-md text-body-md text-on-surface">
                    確認後無法復原。請再次核對目標與JSON Pointer。
                  </p>
                </div>
              </div>
              <dl className="border-b border-outline-variant px-lg py-md">
                <資料列 名稱="目標類型">{targetLabels[confirmation.targetType]}</資料列>
                <資料列 名稱="目標資料列">{代碼(confirmation.targetRowId)}</資料列>
                <資料列 名稱="JSON Pointer">
                  {confirmation.jsonPath === '' ? '整份文件' : 代碼(confirmation.jsonPath)}
                </資料列>
                <資料列 名稱="原因">{confirmation.reason}</資料列>
              </dl>
              <div className="flex justify-end gap-sm p-md">
                <button type="button" disabled={redactionPending} onClick={() => setConfirmation(null)}
                  className="rounded px-4 py-1.5 font-body-md text-body-md font-semibold text-secondary transition-colors hover:bg-secondary/10 disabled:opacity-50">
                  取消
                </button>
                <button type="button" disabled={redactionPending} onClick={() => {
                  void onRedact(confirmation).then(() => setConfirmation(null), () => undefined)
                }}
                  className="rounded bg-error px-4 py-1.5 font-body-md text-body-md font-semibold text-on-error transition-colors hover:bg-error/90 disabled:opacity-50">
                  {redactionPending ? '永久遮蔽中…' : '確認永久遮蔽'}
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    </article>
  )
}

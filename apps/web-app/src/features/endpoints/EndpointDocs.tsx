import { useEffect, useRef, useState } from 'react'
import {
  getOwnerEndpointDocs,
  listCredentials,
  type CredentialSummary,
  type EndpointDocs as EndpointDocsValue,
} from '../../api/endpoints'
import { 卡片, 按鈕, 狀態色調, 狀態標籤, 程式碼區塊, 資料列, 載入中, 複製按鈕, 錯誤訊息 } from '../../ui/元件'
import { 狀態文字 } from '../../ui/格式'

export const ENDPOINT_DOCS_ERROR_MESSAGE = '目前無法載入端點文件，請稍後再試。'

type DocsState =
  | { kind: 'loading' }
  | { kind: 'ready'; docs: EndpointDocsValue; credentials: CredentialSummary[] }
  | { kind: 'error' }

const 憑證查看提示 = '請至 API 存取憑證查看'

function 清理檔名片段(value: string): string {
  const cleaned = value.trim().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  return cleaned.length > 0 ? cleaned.slice(0, 80) : 'endpoint'
}

function 下載Markdown檔案(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function 建立憑證遮罩(credentials: CredentialSummary[]): string {
  const usable = credentials.find((item) => item.status === 'active') ?? credentials[0]
  if (!usable) return 憑證查看提示
  const prefix = usable.keyPrefix.trim()
  const last4 = usable.keyLast4.trim()
  if (!prefix || last4.length !== 4) return 憑證查看提示
  return `${prefix}…${last4}`
}

export function 建立端點交付文件(docs: EndpointDocsValue, credentials: CredentialSummary[] = []): string {
  const endpointTitle = `${docs.endpoint.slug} API`
  const apiKeyDisplay = 建立憑證遮罩(credentials)
  const requestSchema = JSON.stringify(docs.requestSchema, null, 2)
  const responseSchema = JSON.stringify(docs.responseSchema, null, 2)
  const errorRows = docs.errors
    .map((error) => `| ${error.status} | \`${error.code}\` | ${error.message} |`)
    .join('\n')

  return [
    `# ${endpointTitle} — API 交付文件`,
    '',
    '## 基本資訊',
    '',
    `- **API 名稱**：${endpointTitle}`,
    `- **Endpoint URL**：\`${docs.invokeUrl}\``,
    `- **API key**：${apiKeyDisplay === 憑證查看提示 ? apiKeyDisplay : `\`${apiKeyDisplay}\``}`,
    `- **Endpoint ID**：\`${docs.endpoint.id}\``,
    `- **版本**：v${docs.endpoint.version}`,
    `- **狀態**：${狀態文字(docs.endpoint.status)}`,
    '',
    '## 實際呼叫',
    '',
    '```http',
    `POST ${docs.invokeUrl}`,
    `Authorization: Bearer ${apiKeyDisplay === 憑證查看提示 ? '${API_KEY}' : apiKeyDisplay}`,
    'Content-Type: application/json',
    '```',
    '',
    '## 送出請求格式',
    '',
    '```json',
    JSON.stringify({ input: {}, session_id: null, metadata: { endpoint_id: docs.endpoint.id } }, null, 2),
    '```',
    '',
    '> 頂層只接受 `input`、`session_id`、`metadata` 三個欄位。',
    '',
    '## Request schema',
    '',
    '```json',
    requestSchema,
    '```',
    '',
    '## Response schema',
    '',
    '```json',
    responseSchema,
    '```',
    '',
    '## 多輪對話延續（session_id）',
    '',
    '開新對話時 `session_id` 送 `null`；如果回應中帶回新的 `session_id`，同一段對話接下來每次都帶同一個值即可。',
    '',
    '重新開始一段新對話時，再送一次 `null`。',
    '',
    '## cURL 範例',
    '',
    '```bash',
    docs.examples.curl.replace('${API_KEY}', apiKeyDisplay === 憑證查看提示 ? '${API_KEY}' : apiKeyDisplay),
    '```',
    '',
    '## Python 範例',
    '',
    '```python',
    docs.examples.python.replaceAll('${API_KEY}', apiKeyDisplay === 憑證查看提示 ? '${API_KEY}' : apiKeyDisplay),
    '```',
    '',
    '## 錯誤碼一覽',
    '',
    '| HTTP | code | 說明 |',
    '| ---- | ---- | ---- |',
    errorRows,
    '',
  ].join('\n')
}

export default function EndpointDocs({ endpointId }: { endpointId: string }) {
  const [state, setState] = useState<DocsState>({ kind: 'loading' })
  const generation = useRef(0)

  useEffect(() => {
    const current = ++generation.current
    const controller = new AbortController()
    setState({ kind: 'loading' })
    void Promise.all([
      getOwnerEndpointDocs(endpointId, { signal: controller.signal }),
      listCredentials(endpointId, { signal: controller.signal }).then(
        (page) => page.items,
        () => [],
      ),
    ]).then(
      ([docs, credentials]) => {
        if (generation.current === current && !controller.signal.aborted) setState({ kind: 'ready', docs, credentials })
      },
      () => {
        if (generation.current === current && !controller.signal.aborted) setState({ kind: 'error' })
      },
    )
    return () => {
      generation.current += 1
      controller.abort()
    }
  }, [endpointId])

  return (
    <section aria-labelledby="endpoint-docs-title" className="flex flex-col gap-md">
      <h2 id="endpoint-docs-title" className="sr-only">
        Docs
      </h2>
      {state.kind === 'loading' && <載入中>正在載入端點文件…</載入中>}
      {state.kind === 'error' && <錯誤訊息>{ENDPOINT_DOCS_ERROR_MESSAGE}</錯誤訊息>}
      {state.kind === 'ready' && (
        <>
          <卡片
            標題="串接資訊"
            說明="提供給呼叫此端點的工程師。"
            動作={
              <按鈕
                type="button"
                圖示名="文件"
                aria-label="下載 API 交付文件"
                onClick={() => 下載Markdown檔案(
                  `${清理檔名片段(state.docs.endpoint.slug)}-api-docs.md`,
                  建立端點交付文件(state.docs, state.credentials),
                )}
              >
                下載文件
              </按鈕>
            }
          >
            <dl aria-label="端點文件摘要">
              <資料列 名稱="Endpoint">
                <span className="inline-flex items-center gap-sm">
                  <code className="font-code-md text-code-md">{state.docs.endpoint.slug}</code>
                  <狀態標籤 色調="中性">v{state.docs.endpoint.version}</狀態標籤>
                  <狀態標籤 色調={狀態色調(state.docs.endpoint.status)}>
                    {狀態文字(state.docs.endpoint.status)}
                  </狀態標籤>
                </span>
              </資料列>
              <資料列 名稱="Invoke URL">
                <span className="inline-flex items-center gap-sm">
                  <code className="break-all font-code-md text-code-md">{state.docs.invokeUrl}</code>
                </span>
              </資料列>
              <資料列 名稱="Authentication">
                <code className="font-code-md text-code-md">
                  {state.docs.authentication.scheme}／{state.docs.authentication.header}
                </code>
              </資料列>
              <資料列 名稱="Rate limit">
                {state.docs.rateLimit.requests} requests／{state.docs.rateLimit.windowSeconds} seconds
              </資料列>
            </dl>
          </卡片>

          <div className="grid items-stretch gap-md lg:grid-cols-2">
            <卡片 標題="Request schema" className="端點文件Schema卡片 h-full">
              <程式碼區塊
                內容={JSON.stringify(state.docs.requestSchema, null, 2)}
                標籤="request schema"
                className="端點文件Schema碼 h-80"
              />
            </卡片>
            <卡片 標題="Response schema" className="端點文件Schema卡片 h-full">
              <程式碼區塊
                內容={JSON.stringify(state.docs.responseSchema, null, 2)}
                標籤="response schema"
                className="端點文件Schema碼 h-80"
              />
            </卡片>
          </div>

          <卡片 標題="cURL 範例" 動作={<複製按鈕 內容={state.docs.examples.curl} 標籤="cURL 範例" />}>
            <程式碼區塊 內容={state.docs.examples.curl} 可複製={false} />
          </卡片>

          <卡片 標題="Python 範例" 動作={<複製按鈕 內容={state.docs.examples.python} 標籤="Python 範例" />}>
            <程式碼區塊 內容={state.docs.examples.python} 可複製={false} />
          </卡片>

          <卡片 標題="錯誤碼" 無內距={true}>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="bg-surface-container font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                    <th className="border-b border-outline-variant px-md py-sm font-medium">Code</th>
                    <th className="border-b border-outline-variant px-md py-sm font-medium">Status</th>
                    <th className="border-b border-outline-variant px-md py-sm font-medium">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {state.docs.errors.map((error) => (
                    <tr key={error.code} className="border-b border-outline-variant/60 last:border-b-0">
                      <td className="px-md py-2 font-code-md text-code-md text-error">{error.code}</td>
                      <td className="px-md py-2 font-code-md text-code-md text-on-surface-variant">
                        {error.status}
                      </td>
                      <td className="px-md py-2 font-body-md text-body-md text-on-surface">
                        {error.message}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </卡片>
        </>
      )}
    </section>
  )
}

import { useEffect, useRef, useState } from 'react'
import { getOwnerEndpointDocs, type EndpointDocs as EndpointDocsValue } from '../../api/endpoints'
import { 卡片, 狀態色調, 狀態標籤, 程式碼區塊, 資料列, 載入中, 複製按鈕, 錯誤訊息 } from '../../ui/元件'
import { 狀態文字 } from '../../ui/格式'

export const ENDPOINT_DOCS_ERROR_MESSAGE = '目前無法載入端點文件，請稍後再試。'

type DocsState =
  | { kind: 'loading' }
  | { kind: 'ready'; docs: EndpointDocsValue }
  | { kind: 'error' }

export default function EndpointDocs({ endpointId }: { endpointId: string }) {
  const [state, setState] = useState<DocsState>({ kind: 'loading' })
  const generation = useRef(0)

  useEffect(() => {
    const current = ++generation.current
    const controller = new AbortController()
    setState({ kind: 'loading' })
    void getOwnerEndpointDocs(endpointId, { signal: controller.signal }).then(
      (docs) => {
        if (generation.current === current && !controller.signal.aborted) setState({ kind: 'ready', docs })
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
          <卡片 標題="串接資訊" 說明="提供給呼叫此端點的工程師。">
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

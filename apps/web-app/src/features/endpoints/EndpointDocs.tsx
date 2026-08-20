import { useEffect, useRef, useState } from 'react'
import { getOwnerEndpointDocs, type EndpointDocs as EndpointDocsValue } from '../../api/endpoints'

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

  return <section aria-labelledby="endpoint-docs-title">
    <h2 id="endpoint-docs-title">Docs</h2>
    {state.kind === 'loading' && <p role="status">正在載入端點文件…</p>}
    {state.kind === 'error' && <p role="alert">{ENDPOINT_DOCS_ERROR_MESSAGE}</p>}
    {state.kind === 'ready' && <div>
      <dl aria-label="端點文件摘要">
        <dt>Endpoint</dt><dd>{state.docs.endpoint.slug}（v{state.docs.endpoint.version}／{state.docs.endpoint.status}）</dd>
        <dt>Invoke URL</dt><dd><code>{state.docs.invokeUrl}</code></dd>
        <dt>Authentication</dt><dd>{state.docs.authentication.scheme}／{state.docs.authentication.header}</dd>
        <dt>Rate limit</dt><dd>{state.docs.rateLimit.requests} requests／{state.docs.rateLimit.windowSeconds} seconds</dd>
      </dl>
      <h3>Request schema</h3>
      <pre>{JSON.stringify(state.docs.requestSchema, null, 2)}</pre>
      <h3>Response schema</h3>
      <pre>{JSON.stringify(state.docs.responseSchema, null, 2)}</pre>
      <h3>cURL example</h3>
      <pre>{state.docs.examples.curl}</pre>
      <h3>Python example</h3>
      <pre>{state.docs.examples.python}</pre>
      <h3>Errors</h3>
      <table><thead><tr><th>Code</th><th>Status</th><th>Message</th></tr></thead>
        <tbody>{state.docs.errors.map((error) => <tr key={error.code}>
          <td>{error.code}</td><td>{error.status}</td><td>{error.message}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>
}

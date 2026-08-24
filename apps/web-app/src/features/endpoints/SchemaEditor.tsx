import type { DraftReceipt } from '../../api/endpoints'

export default function SchemaEditor({ preview }: { preview: DraftReceipt['preview'] }) {
  return (
    <section aria-labelledby="server-preview-title">
      <h2 id="server-preview-title">Server Preview（唯讀）</h2>
      <h3>System prompt</h3>
      <pre data-preview-field={true}>{preview.systemPrompt}</pre>
      <h3>Input schema</h3>
      <pre data-preview-field={true}>{JSON.stringify(preview.inputSchema, null, 2)}</pre>
      <h3>Response schema</h3>
      <pre data-preview-field={true}>{JSON.stringify(preview.responseSchema, null, 2)}</pre>
      <h3>Human docs</h3>
      <pre data-preview-field={true}>{preview.humanDocs}</pre>
    </section>
  )
}

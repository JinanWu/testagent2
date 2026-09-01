import type { ReactNode } from 'react'
import type { DraftReceipt } from '../../api/endpoints'
import { 複製按鈕 } from '../../ui/元件'

/*
 * 伺服器產生的草案預覽（唯讀）。
 *
 * 原本是四個分頁，要一個個點才看得到內容；但這一頁的任務是「發布前把伺服器
 * 產生的東西核對過一遍」，藏起來三份等於預設使用者不會看。改成全部攤開、
 * 依閱讀順序往下排：先講這支 API 是什麼，再看 prompt，最後看輸入輸出結構。
 */

function 區段({
  標題,
  複製內容,
  children,
}: {
  標題: string
  複製內容?: string
  children: ReactNode
}) {
  return (
    <section className="flex flex-col gap-sm">
      <div className="flex items-center justify-between gap-md">
        <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
          {標題}
        </h3>
        {複製內容 !== undefined && <複製按鈕 內容={複製內容} 標籤={標題} />}
      </div>
      {children}
    </section>
  )
}

function 標記({ children }: { children: ReactNode }) {
  return (
    <li className="rounded-full border border-outline-variant bg-surface-container px-3 py-1 font-label-sm text-label-sm text-on-surface">
      {children}
    </li>
  )
}

export default function SchemaEditor({ preview }: { preview: DraftReceipt['preview'] }) {
  const inputSchema = JSON.stringify(preview.inputSchema, null, 2)
  const responseSchema = JSON.stringify(preview.responseSchema, null, 2)

  return (
    <section aria-labelledby="server-preview-title" className="flex flex-col gap-xl">
      <h2 id="server-preview-title" className="sr-only">
        Server Preview（唯讀）
      </h2>

      {preview.selectedSkills.length > 0 && (
        <區段 標題="使用的 Skills">
          <ul className="flex flex-wrap gap-xs">
            {preview.selectedSkills.map((skill) => (
              <標記 key={skill}>{skill}</標記>
            ))}
          </ul>
        </區段>
      )}

      {preview.recommendedTools.length > 0 && (
        <區段 標題="伺服器建議的工具">
          <ul className="flex flex-wrap gap-xs">
            {preview.recommendedTools.map((tool) => (
              <標記 key={tool}>
                {tool}
                {preview.toolCapabilities[tool] ? `：${preview.toolCapabilities[tool]}` : ''}
              </標記>
            ))}
          </ul>
        </區段>
      )}

      {/* 長段文字用內文字體呈現，不塞進等寬程式碼區塊——這是要讀的，不是要複製貼上的程式碼 */}
      <區段 標題="System prompt（唯讀）" 複製內容={preview.systemPrompt}>
        <p
          data-preview-field={true}
          className="max-h-[26rem] overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-outline-variant bg-surface-container-low p-lg font-body-lg text-body-lg text-on-surface"
        >
          {preview.systemPrompt}
        </p>
      </區段>

      <區段 標題="Schema 定義（唯讀）">
        <div className="grid items-stretch gap-md lg:grid-cols-2">
          <div className="flex h-full min-w-0 flex-col gap-xs">
            <div className="flex items-center justify-between gap-sm">
              <h4 className="font-body-md text-body-md font-semibold text-on-surface">Input schema</h4>
              <複製按鈕 內容={inputSchema} 標籤="Input schema" />
            </div>
            {/*
              兩邊固定同高：input_schema 可能是 null（一行），response_schema 動輒十幾行，
              用 max-h 會變成一高一矮。固定高度＋內部捲動，長的塞得下、短的不留白。
            */}
            <pre data-preview-field={true} className="程式碼區塊 h-80 overflow-auto">
              {inputSchema}
            </pre>
          </div>
          <div className="flex h-full min-w-0 flex-col gap-xs">
            <div className="flex items-center justify-between gap-sm">
              <h4 className="font-body-md text-body-md font-semibold text-on-surface">Response schema</h4>
              <複製按鈕 內容={responseSchema} 標籤="Response schema" />
            </div>
            <pre data-preview-field={true} className="程式碼區塊 h-80 overflow-auto">
              {responseSchema}
            </pre>
          </div>
        </div>
      </區段>

      <區段 標題="Human docs（唯讀）" 複製內容={preview.humanDocs}>
        <p
          data-preview-field={true}
          className="max-h-[26rem] overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-outline-variant bg-surface-container-low p-lg font-body-lg text-body-lg text-on-surface"
        >
          {preview.humanDocs}
        </p>
      </區段>
    </section>
  )
}

import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import 圖示, { type 圖示名稱 } from './圖示'

/* ── 按鈕 ───────────────────────────────────────────────── */

export type 按鈕樣式 = '主要' | '次要' | '危險' | '文字'

const 按鈕樣式表: Record<按鈕樣式, string> = {
  主要: 'bg-secondary text-on-secondary hover:bg-secondary/90 border border-transparent',
  次要:
    'bg-surface-container-lowest text-on-surface border border-outline-variant hover:bg-surface-container',
  危險: 'bg-error text-on-error hover:bg-error/90 border border-transparent',
  文字: 'bg-transparent text-secondary border border-transparent hover:bg-secondary/10',
}

export interface 按鈕屬性 extends ButtonHTMLAttributes<HTMLButtonElement> {
  樣式?: 按鈕樣式
  圖示名?: 圖示名稱
}

export function 按鈕({ 樣式 = '次要', 圖示名, className = '', children, ...其餘 }: 按鈕屬性) {
  return (
    <button
      {...其餘}
      className={[
        'inline-flex items-center justify-center gap-sm rounded px-4 py-1.5',
        'font-body-md text-body-md font-semibold transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        按鈕樣式表[樣式],
        className,
      ].join(' ')}
    >
      {圖示名 && <圖示 名稱={圖示名} 大小={16} />}
      {children}
    </button>
  )
}

/* ── 卡片 ───────────────────────────────────────────────── */

export function 卡片({
  標題,
  說明,
  動作,
  無內距 = false,
  className = '',
  children,
}: {
  標題?: ReactNode
  說明?: ReactNode
  動作?: ReactNode
  無內距?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    /*
     * 卡片刻意只留一層邊框、標題列不再填底色也不畫分隔線：
     * 標題與內容之間靠留白分組就夠了，多一條線就會出現「框中框」的僵硬感。
     * 只有 無內距（表格、清單那類自帶列線的內容）才需要那條分隔線。
     */
    <section
      className={[
        'overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest',
        'shadow-[0_1px_3px_rgba(15,23,42,0.04)]',
        className,
      ].join(' ')}
    >
      {(標題 || 動作) && (
        <div
          className={[
            'flex items-start justify-between gap-md px-lg pt-lg',
            無內距 ? 'border-b border-outline-variant pb-md' : 'pb-md',
          ].join(' ')}
        >
          <div className="min-w-0">
            {標題 && (
              <h2 className="font-headline-sm text-headline-sm text-on-surface">{標題}</h2>
            )}
            {說明 && (
              <p className="mt-xs font-body-md text-body-md text-on-surface-variant">{說明}</p>
            )}
          </div>
          {動作 && <div className="flex shrink-0 items-center gap-sm">{動作}</div>}
        </div>
      )}
      <div className={無內距 ? '' : 'px-lg pb-lg'}>{children}</div>
    </section>
  )
}

/* ── 狀態標籤 ───────────────────────────────────────────── */

export type 標籤色調 = '成功' | '中性' | '警示' | '錯誤' | '資訊'

const 標籤樣式表: Record<標籤色調, string> = {
  成功: 'bg-tertiary-fixed text-on-tertiary-fixed border-tertiary-fixed-dim',
  中性: 'bg-surface-container-high text-on-surface-variant border-outline-variant',
  警示: 'bg-amber-100 text-amber-900 border-amber-300',
  錯誤: 'bg-error-container text-on-error-container border-error/30',
  資訊: 'bg-secondary-fixed text-on-secondary-fixed border-secondary-fixed-dim',
}

export function 狀態標籤({
  色調 = '中性',
  children,
}: {
  色調?: 標籤色調
  children: ReactNode
}) {
  return (
    <span
      className={[
        'inline-flex items-center gap-1 rounded border px-2 py-0.5',
        'font-label-sm text-label-sm whitespace-nowrap',
        標籤樣式表[色調],
      ].join(' ')}
    >
      {children}
    </span>
  )
}

/** 端點 / 憑證的狀態字串對應到標籤色調與中文說明。 */
export function 狀態色調(狀態: string): 標籤色調 {
  if (狀態 === 'active' || 狀態 === 'succeeded') return '成功'
  if (狀態 === 'archived' || 狀態 === 'expired' || 狀態 === 'pending') return '警示'
  if (狀態 === 'failed' || 狀態 === 'revoked' || 狀態 === 'invalid_api_key') return '錯誤'
  if (狀態 === 'running' || 狀態 === 'rate_limited') return '資訊'
  return '中性'
}

/* ── 程式碼區塊 ─────────────────────────────────────────── */

export function 程式碼區塊({
  內容,
  標籤,
  可複製 = true,
  className = '',
}: {
  內容: string
  標籤?: string
  可複製?: boolean
  className?: string
}) {
  return (
    <div className={['relative', className].join(' ')}>
      {可複製 && (
        <div className="absolute right-2 top-2 z-10">
          <複製按鈕 內容={內容} 標籤={標籤} 深底={true} />
        </div>
      )}
      <pre className={['程式碼區塊', 可複製 ? 'pr-24' : ''].join(' ')}>{內容}</pre>
    </div>
  )
}

/*
 * 複製按鈕有兩種擺法：疊在深色程式碼區塊上（深底），或是放在一般表面的標題列旁。
 * 原本只有前者的白色樣式，放到淺色卡片上會變成白字白底看不見。
 */
export function 複製按鈕({ 內容, 標籤, 深底 = false }: { 內容: string; 標籤?: string; 深底?: boolean }) {
  const [已複製, 設已複製] = useState(false)
  const 計時器 = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (計時器.current !== null) clearTimeout(計時器.current)
    },
    [],
  )

  async function 複製() {
    try {
      await navigator.clipboard.writeText(內容)
      設已複製(true)
      if (計時器.current !== null) clearTimeout(計時器.current)
      計時器.current = setTimeout(() => 設已複製(false), 2000)
    } catch {
      /* 瀏覽器拒絕存取剪貼簿時維持原狀，使用者仍可手動選取。 */
    }
  }

  return (
    <button
      type="button"
      onClick={() => void 複製()}
      aria-label={標籤 ? `複製${標籤}` : '複製'}
      className={[
        'inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 font-label-sm text-label-sm transition-colors',
        深底
          ? 'bg-white/10 text-white hover:bg-white/20'
          : 'border border-outline-variant text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
      ].join(' ')}
    >
      <圖示 名稱={已複製 ? '成功' : '複製'} 大小={14} />
      {已複製 ? '已複製' : '複製'}
    </button>
  )
}

/* ── 狀態訊息 ───────────────────────────────────────────── */

/*
 * 圖示一律放在 role="alert" / role="status" 元素之外，
 * 讓這些元素的子節點維持純文字，可及性樹與既有測試斷言都不受裝飾影響。
 */

export function 載入中({ children = '載入中…' }: { children?: ReactNode }) {
  return (
    <div className="flex items-center gap-sm py-md text-on-surface-variant">
      <span
        aria-hidden={true}
        className="size-4 shrink-0 animate-spin rounded-full border-2 border-outline-variant border-t-secondary"
      />
      <p role="status" aria-live="polite" className="font-body-md text-body-md">
        {children}
      </p>
    </div>
  )
}

export function 錯誤訊息({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-sm rounded border border-error/30 bg-error-container px-md py-sm text-on-error-container">
      <span aria-hidden={true} className="mt-0.5 shrink-0">
        <圖示 名稱="錯誤" 大小={16} />
      </span>
      <p role="alert" className="font-body-md text-body-md">
        {children}
      </p>
    </div>
  )
}

export function 成功訊息({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-sm rounded border border-tertiary-fixed-dim bg-tertiary-fixed px-md py-sm text-on-tertiary-fixed">
      <span aria-hidden={true} className="mt-0.5 shrink-0">
        <圖示 名稱="成功" 大小={16} />
      </span>
      <p role="status" className="font-body-md text-body-md">
        {children}
      </p>
    </div>
  )
}

export function 空狀態({ children }: { children: ReactNode }) {
  return (
    <p className="py-lg text-center font-body-md text-body-md text-on-surface-variant">
      {children}
    </p>
  )
}

/* ── 表單欄位 ───────────────────────────────────────────── */

export function 欄位({
  標籤,
  htmlFor,
  提示,
  className = '',
  children,
}: {
  標籤: ReactNode
  htmlFor: string
  提示?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <div className={['flex flex-col gap-xs', className].join(' ')}>
      <label
        htmlFor={htmlFor}
        className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface"
      >
        {標籤}
      </label>
      {children}
      {提示 && <p className="font-body-md text-xs text-on-surface-variant">{提示}</p>}
    </div>
  )
}

/** 輸入類元件共用的樣式，供頁面直接套在原生 input / textarea / select 上。 */
export const 輸入樣式 =
  'w-full rounded border border-outline-variant bg-surface-container-lowest px-3 py-2 ' +
  'font-body-md text-body-md text-on-surface placeholder:text-placeholder ' +
  'disabled:cursor-not-allowed disabled:bg-surface-container disabled:opacity-60'

export const 等寬輸入樣式 = 輸入樣式 + ' font-code-md text-code-md'

/* ── 定義清單 ───────────────────────────────────────────── */

export function 資料列({ 名稱, children }: { 名稱: ReactNode; children: ReactNode }) {
  return (
    /*
     * 名稱不再全大寫：中英文標籤混排時，「SLUG」旁邊擺「狀態」看起來像兩套系統。
     * 分隔線也調淡，靠列高留白分組就夠。
     */
    <div className="flex flex-wrap items-baseline justify-between gap-md border-b border-outline-variant/40 py-3 last:border-b-0">
      <dt className="font-body-md text-body-md text-on-surface-variant">{名稱}</dt>
      <dd className="min-w-0 break-words text-right font-body-md text-body-md text-on-surface">
        {children}
      </dd>
    </div>
  )
}

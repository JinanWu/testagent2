import type { ReactNode } from 'react'
import { useSession } from '../app/SessionProvider'
import 圖示 from './圖示'
import 主題切換 from './主題切換'

export type 分頁代號 = '對話' | '端點' | '稽核'

interface 導覽項目 {
  代號: 分頁代號
  標籤: string
  圖示類別: string
  僅管理者: boolean
}

/*
 * 標籤沿用既有產品用語（不改成設計稿的「稽核紀錄」）：
 * 權限測試以「畫面上不得出現『完整呼叫紀錄』」來驗證 member 看不到管理員入口，
 * 改字會讓那條斷言變成永遠成立而失去保護作用。
 */
const 導覽清單: readonly 導覽項目[] = [
  { 代號: '對話', 標籤: '對話', 圖示類別: '導覽項目-對話', 僅管理者: false },
  { 代號: '端點', 標籤: '端點管理', 圖示類別: '導覽項目-端點', 僅管理者: false },
  { 代號: '稽核', 標籤: '完整呼叫紀錄', 圖示類別: '導覽項目-稽核', 僅管理者: true },
]

const 角色名稱: Record<string, string> = { admin: '管理員', member: '成員' }

function 角色標籤(角色?: string) {
  if (!角色) return ''
  return 角色名稱[角色] ?? 角色
}

export interface 應用框架屬性 {
  目前分頁: 分頁代號
  標題: string
  標題Id?: string
  副標題?: string
  工具列?: ReactNode
  側欄頂部?: ReactNode
  側欄額外?: ReactNode
  滿版?: boolean
  /* 空白對話那類「整頁式」畫面不畫標題列分隔線 */
  分隔線?: boolean
  /*
   * 整頁式畫面不顯示標題列。標題節點仍留在 DOM（sr-only）：
   * h1 是這個畫面的無障礙名稱，既有測試也以 id 取它。
   */
  標題可見?: boolean
  on開啟對話?: () => void
  on開啟端點?: () => void
  on開啟稽核?: () => void
  on登出: () => void
  登出中?: boolean
  children: ReactNode
}

export default function 應用框架({
  目前分頁,
  標題,
  標題Id,
  副標題,
  工具列,
  側欄頂部,
  側欄額外,
  滿版 = false,
  分隔線 = true,
  標題可見 = true,
  on開啟對話,
  on開啟端點,
  on開啟稽核,
  on登出,
  登出中 = false,
  children,
}: 應用框架屬性) {
  const { user } = useSession()
  const 開啟處理器: Record<分頁代號, (() => void) | undefined> = {
    對話: on開啟對話,
    端點: on開啟端點,
    稽核: on開啟稽核,
  }

  return (
    <div className="app-shell flex h-screen flex-col overflow-hidden bg-background text-on-background">
      {/* TopAppBar：品牌在左，身分區在右（設計需求 §2 要求 username 必須被看見） */}
      <header className="flex h-16 shrink-0 items-center justify-between gap-md border-b border-outline-variant bg-surface px-lg">
        <div className="flex min-w-0 items-center gap-sm">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-container text-on-primary-container">
            <圖示 名稱="標誌" 大小={18} />
          </span>
          <p className="truncate font-headline-md text-headline-md font-bold text-on-surface">
            ColaX
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-sm">
          <主題切換 />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="主導覽"
          className="flex w-sidebar-width shrink-0 flex-col border-r border-outline-variant bg-surface-container-low py-lg"
        >
          {側欄頂部 && <div className="px-md pb-lg">{側欄頂部}</div>}

          <p className="px-lg pb-sm font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
            導覽
          </p>
          <ul className="flex flex-col gap-xs px-md">
            {導覽清單.map((項目) => {
              if (項目.僅管理者 && user?.role !== 'admin') return null
              const 是目前 = 項目.代號 === 目前分頁
              const 處理器 = 開啟處理器[項目.代號]
              return (
                <li key={項目.代號}>
                  {/*
                    子節點維持純字串：既有測試以 children.join('') 比對標籤取得這些按鈕。
                    圖示與 ADMIN 標記都由 .導覽項目-* 的偽元素繪製，不進入 DOM。
                  */}
                  <button
                    type="button"
                    aria-current={是目前 ? 'page' : undefined}
                    disabled={是目前 || !處理器}
                    onClick={處理器}
                    className={[
                      '導覽項目',
                      項目.圖示類別,
                      'w-full rounded-xl px-2 py-2 text-left font-body-md text-body-md transition-colors',
                      是目前
                        ? 'bg-primary-container font-bold text-on-primary-container'
                        : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface',
                    ].join(' ')}
                  >
                    {項目.標籤}
                  </button>
                </li>
              )
            })}
          </ul>

          {側欄額外}

          {/* 身分區固定在左下角：頭像縮寫 ＋ 帳號 ＋ 角色，登出只留一顆小圖示鈕 */}
          <div className="mt-auto px-md pt-md">
            <div className="flex items-center gap-sm rounded-xl bg-surface-container px-sm py-2">
              <span
                aria-hidden={true}
                className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-container font-label-sm text-label-sm font-bold uppercase text-on-primary-container"
              >
                {(user?.username ?? '').slice(0, 2)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate font-body-md text-body-md font-semibold text-on-surface">
                  {user?.username}
                </p>
                <p className="truncate font-label-sm text-label-sm text-on-surface-variant">
                  {角色標籤(user?.role)}
                </p>
              </div>
              {/*
                此按鈕的子節點刻意維持純字串：既有測試以 findByProps({ children: '登出' })
                取得它並直接呼叫 onClick，包一層 <span> 或加圖示都會讓斷言落在錯的節點上。
                因此圖示走偽元素，文字則由 .導覽項目-僅圖示 的 font-size: 0 隱藏
                （節點與無障礙名稱都還在，只是不佔版面）。
              */}
              <button
                type="button"
                title={登出中 ? '登出中…' : '登出'}
                disabled={登出中}
                onClick={on登出}
                className="導覽項目 導覽項目-登出 導覽項目-僅圖示 flex size-8 shrink-0 items-center justify-center rounded text-on-surface-variant transition-colors hover:bg-error-container/40 hover:text-error disabled:opacity-60"
              >
                {登出中 ? '登出中…' : '登出'}
              </button>
            </div>
          </div>
        </nav>

        <div className="flex min-h-0 flex-1 flex-col">
          <header
            className={
              標題可見
                ? [
                    'flex min-h-16 shrink-0 items-center justify-between gap-md bg-surface px-xl py-sm',
                    分隔線 ? 'border-b border-outline-variant' : '',
                  ].join(' ')
                : 'sr-only'
            }
          >
            <div className="min-w-0">
              <h1 id={標題Id} className="truncate font-headline-sm text-headline-sm text-on-surface">
                {標題}
              </h1>
              {副標題 && (
                <p className="truncate font-body-md text-body-md text-on-surface-variant">
                  {副標題}
                </p>
              )}
            </div>
            {工具列 && <div className="flex shrink-0 items-center gap-sm">{工具列}</div>}
          </header>

          {滿版 ? (
            children
          ) : (
            <main className="應用主內容 min-h-0 flex-1 overflow-y-auto p-xl">
              <div className="mx-auto w-full max-w-[1680px]">{children}</div>
            </main>
          )}
        </div>
      </div>
    </div>
  )
}

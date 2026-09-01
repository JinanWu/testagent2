import { useEffect, useMemo, useRef, useState } from 'react'
import { getSkill, listSkills, type SkillDetail, type SkillSummary } from '../../api/skills'
import { 載入中, 空狀態, 錯誤訊息, 輸入樣式 } from '../../ui/元件'
import 圖示 from '../../ui/圖示'

const 每頁 = 8

export interface SkillBrowserProps {
  selected: readonly string[]
  disabled?: boolean
  onSelectedChange(selected: string[]): void
}

export default function SkillBrowser({ selected, disabled = false, onSelectedChange }: SkillBrowserProps) {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [detail, setDetail] = useState<SkillDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [搜尋, 設搜尋] = useState('')
  const [頁碼, 設頁碼] = useState(1)
  const listController = useRef<AbortController | null>(null)
  const detailController = useRef<AbortController | null>(null)
  const detailEpoch = useRef(0)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    listController.current = controller
    void listSkills(controller.signal).then(
      (items) => {
        if (!mounted.current || controller.signal.aborted) return
        setSkills([...items].sort((left, right) => left.name.localeCompare(right.name) || left.id.localeCompare(right.id)))
      },
      () => { if (mounted.current && !controller.signal.aborted) setError(true) },
    ).finally(() => { if (mounted.current && !controller.signal.aborted) setLoading(false) })
    return () => {
      mounted.current = false
      controller.abort()
      detailController.current?.abort()
      detailEpoch.current += 1
    }
  }, [])

  function toggle(id: string, checked: boolean) {
    const next = checked ? [...new Set([...selected, id])].sort() : selected.filter((item) => item !== id)
    if (next.length <= 32) onSelectedChange(next)
  }

  function openDetail(skill: SkillSummary) {
    detailController.current?.abort()
    const controller = new AbortController()
    detailController.current = controller
    const epoch = ++detailEpoch.current
    setDetail(null)
    void getSkill(skill.id, controller.signal).then(
      (value) => {
        if (mounted.current && !controller.signal.aborted && detailEpoch.current === epoch) setDetail(value)
      },
      () => { if (mounted.current && !controller.signal.aborted && detailEpoch.current === epoch) setError(true) },
    )
  }

  /*
   * 近百個 Skills：搜尋是唯一的收斂手段（分類有二十幾種，做成快捷列反而是三排雜訊；
   * 分類名稱一樣吃得到搜尋）。
   */
  const 篩選後 = useMemo(() => {
    const 關鍵字 = 搜尋.trim().toLowerCase()
    if (關鍵字.length === 0) return skills
    return skills.filter((skill) =>
      skill.name.toLowerCase().includes(關鍵字) ||
      skill.description.toLowerCase().includes(關鍵字) ||
      skill.category.toLowerCase().includes(關鍵字),
    )
  }, [skills, 搜尋])

  /* 分頁：不讓頁面無限往下長，一頁固定 8 張卡片 */
  const 總頁數 = Math.max(1, Math.ceil(篩選後.length / 每頁))
  const 目前頁 = Math.min(頁碼, 總頁數)
  const 本頁 = 篩選後.slice((目前頁 - 1) * 每頁, 目前頁 * 每頁)

  const 已選技能 = useMemo(
    () => selected.map((id) => skills.find((skill) => skill.id === id)).filter((skill): skill is SkillSummary => skill !== undefined),
    [selected, skills],
  )

  const 已滿 = selected.length >= 32
  const 有條件 = 搜尋.trim().length > 0

  return (
    <section aria-labelledby="skill-browser-title" className="flex flex-col gap-lg">
      <h2 id="skill-browser-title" className="sr-only">
        選擇 Skills
      </h2>

      <div className="flex flex-wrap items-center gap-sm">
        <div className="relative min-w-[16rem] flex-1">
          <label htmlFor="skill-search" className="sr-only">
            搜尋 Skills
          </label>
          <span aria-hidden={true} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
            <圖示 名稱="搜尋" 大小={18} />
          </span>
          <input
            id="skill-search"
            type="search"
            value={搜尋}
            placeholder="搜尋名稱、說明或分類"
            onChange={(event) => { 設搜尋(event.target.value); 設頁碼(1) }}
            className={`${輸入樣式} pl-10`}
          />
        </div>
        <p
          className={[
            'shrink-0 rounded-lg border px-3 py-2 font-label-sm text-label-sm',
            已滿
              ? 'border-error/30 bg-error-container text-on-error-container'
              : 'border-outline-variant bg-surface-container text-on-surface-variant',
          ].join(' ')}
        >
          已選 {selected.length}／32
        </p>
      </div>

      {已選技能.length > 0 && (
        <ul aria-label="已選 Skills" className="flex flex-wrap gap-xs">
          {已選技能.map((skill) => (
            <li key={skill.id}>
              <span className="flex items-center gap-xs rounded-full border border-primary/40 bg-primary-container/10 py-1 pl-3 pr-1 font-label-sm text-label-sm text-on-surface">
                {skill.name}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => toggle(skill.id, false)}
                  className="flex size-6 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-error-container hover:text-on-error-container disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <圖示 名稱="關閉" 大小={14} />
                  <span className="sr-only">取消選擇 {skill.name}</span>
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {loading && <載入中>正在載入 Skills…</載入中>}
      {error && <錯誤訊息>目前無法載入 Skills，請稍後再試。</錯誤訊息>}
      {!loading && !error && skills.length === 0 && <空狀態>目前沒有可用的 Skills。</空狀態>}

      {!loading && !error && skills.length > 0 && 篩選後.length === 0 && (
        <div className="rounded-xl border border-dashed border-outline-variant px-lg py-xl text-center">
          <p className="font-body-md text-body-md text-on-surface">找不到符合的 Skills。</p>
          <p className="mt-xs font-body-md text-body-md text-on-surface-variant">
            試試更短的關鍵字，或用分類名稱搜尋。
          </p>
          {有條件 && (
            <button
              type="button"
              onClick={() => { 設搜尋(''); 設頁碼(1) }}
              className="mt-md rounded-lg border border-outline-variant px-4 py-2 font-body-md text-body-md text-on-surface transition-colors hover:bg-surface-container-highest"
            >
              清除篩選
            </button>
          )}
        </div>
      )}

      {/* 沒有內層捲動框：整頁只留一條捲軸；長度則由分頁控制，不會無限往下長 */}
      <ul aria-label="Skills 清單" className="grid gap-md sm:grid-cols-2">
        {本頁.map((skill) => {
          const 已選 = selected.includes(skill.id)
          return (
            <li
              key={skill.id}
              onClick={(event) => {
                if (disabled) return
                const target = event.target as HTMLElement
                if (target.closest('button,input,label')) return
                toggle(skill.id, !已選)
              }}
              className={[
                'flex cursor-pointer flex-col rounded-xl border transition-colors',
                已選
                  ? 'border-primary bg-primary-container/10'
                  : 'border-outline-variant bg-surface-container-lowest hover:border-outline',
              ].join(' ')}
            >
              {/*
                技能名稱刻意維持為 label 的直接文字節點：既有測試以
                label.children.flat().join('') 檢查名稱與排序，包進 <span> 會讓名稱消失。
                版面改以 grid 處理，說明文字才另外包 span。
              */}
              <label
                htmlFor={`skill-${skill.id}`}
                className="grid flex-1 cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-sm gap-y-xs p-md font-body-md text-body-md font-semibold text-on-surface"
              >
                <input
                  id={`skill-${skill.id}`}
                  type="checkbox"
                  checked={已選}
                  disabled={disabled}
                  onChange={(event) => toggle(skill.id, event.currentTarget?.checked ?? event.target.checked)}
                  className="row-span-2 mt-1 shrink-0"
                />
                {skill.name}
                {/* 長說明收成兩行，完整內容留給「查看」 */}
                <span className="line-clamp-2 font-body-md text-body-md font-normal text-on-surface-variant">
                  {skill.description}
                </span>
              </label>

              <div className="flex items-center justify-between gap-sm px-md pb-md pl-11">
                {skill.category ? (
                  <span className="truncate rounded-full bg-surface-container px-2 py-0.5 font-label-sm text-label-sm text-on-surface-variant">
                    {skill.category}
                  </span>
                ) : (
                  <span />
                )}
                {/*
                  按鈕文字只留「查看」：技能名稱動輒二十幾個字，重複印在按鈕上會把卡片撐爆。
                  完整名稱放在 aria-label，螢幕閱讀器與測試都還取得到。
                */}
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={`查看 ${skill.name}`}
                  onClick={() => openDetail(skill)}
                  className="shrink-0 rounded-lg px-3 py-1 font-body-md text-body-md text-primary transition-colors hover:bg-primary-container/10 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  查看
                </button>
              </div>
            </li>
          )
        })}
      </ul>

      {總頁數 > 1 && (
        <nav aria-label="Skills 分頁" className="flex items-center justify-between gap-md">
          <button
            type="button"
            disabled={目前頁 <= 1}
            onClick={() => 設頁碼(目前頁 - 1)}
            className="rounded-lg border border-outline-variant px-4 py-2 font-body-md text-body-md text-on-surface transition-colors hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-40"
          >
            上一頁
          </button>
          <p aria-live="polite" className="font-body-md text-body-md text-on-surface-variant">
            第 {目前頁} / {總頁數} 頁 · 共 {篩選後.length} 個
          </p>
          <button
            type="button"
            disabled={目前頁 >= 總頁數}
            onClick={() => 設頁碼(目前頁 + 1)}
            className="rounded-lg border border-outline-variant px-4 py-2 font-body-md text-body-md text-on-surface transition-colors hover:bg-surface-container-highest disabled:cursor-not-allowed disabled:opacity-40"
          >
            下一頁
          </button>
        </nav>
      )}

      {detail && (
        <article
          aria-labelledby="skill-detail-title"
          className="overflow-hidden rounded-xl border border-primary/40"
        >
          <div className="flex items-center justify-between gap-md px-lg py-md">
            <h3 id="skill-detail-title" className="font-headline-sm text-headline-sm text-on-surface">
              {detail.name}
            </h3>
            <button
              type="button"
              onClick={() => { detailEpoch.current += 1; setDetail(null) }}
              className="flex size-8 shrink-0 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
            >
              <圖示 名稱="關閉" 大小={16} />
              <span className="sr-only">關閉 {detail.name} 的內容</span>
            </button>
          </div>
          <p className="max-h-[28rem] overflow-y-auto whitespace-pre-wrap break-words border-t border-outline-variant px-lg py-md font-body-md text-body-md text-on-surface">
            {detail.content}
          </p>
        </article>
      )}
    </section>
  )
}

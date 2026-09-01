import { useCallback, useEffect, useState } from 'react'
import 圖示 from './圖示'

/*
 * 深淺模式切換。
 *
 * 真正決定主題的是 index.html 裡那段 pre-paint script（首次繪製前就要決定，
 * 否則會閃一下另一個色系）。這個元件只負責之後的切換與記憶。
 *
 * 測試環境是 node：沒有 document，window 也是被 stub 過的空殼（只有
 * location / history / addEventListener）。所以每一次瀏覽器 API 存取都必須
 * 先確認存在，不能假設。
 */

const 儲存鍵 = 'testagent2-theme'

export type 主題 = 'light' | 'dark'

const 主題色: Record<主題, string> = { light: '#f7f9fb', dark: '#0b1326' }

function 讀取偏好(): 主題 | null {
  try {
    if (typeof localStorage === 'undefined') return null
    const 值 = localStorage.getItem(儲存鍵)
    return 值 === 'dark' || 值 === 'light' ? 值 : null
  } catch {
    // 無痕模式或封鎖 storage 時直接視為沒有偏好
    return null
  }
}

function 讀取目前主題(): 主題 {
  if (typeof document === 'undefined') return 讀取偏好() ?? 'light'
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

export default function 主題切換() {
  const [主題值, set主題值] = useState<主題>(讀取目前主題)

  const 套用主題 = useCallback((下一個: 主題, 記住: boolean) => {
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('dark', 下一個 === 'dark')
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute('content', 主題色[下一個])
    }
    if (記住) {
      try {
        if (typeof localStorage !== 'undefined') localStorage.setItem(儲存鍵, 下一個)
      } catch {
        // 存不進去不影響本次切換
      }
    }
    set主題值(下一個)
  }, [])

  // 使用者沒手動選過時跟隨系統設定
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const 媒體查詢 = window.matchMedia('(prefers-color-scheme: dark)')
    const 處理變更 = (事件: MediaQueryListEvent) => {
      if (讀取偏好() === null) 套用主題(事件.matches ? 'dark' : 'light', false)
    }
    媒體查詢.addEventListener('change', 處理變更)
    return () => 媒體查詢.removeEventListener('change', 處理變更)
  }, [套用主題])

  const 是深色 = 主題值 === 'dark'

  return (
    <button
      type="button"
      aria-pressed={是深色}
      onClick={() => 套用主題(是深色 ? 'light' : 'dark', true)}
      className="flex size-9 shrink-0 items-center justify-center rounded text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
    >
      <圖示 名稱={是深色 ? '深色模式' : '淺色模式'} 大小={18} />
      <span className="sr-only">{是深色 ? '切換為淺色模式' : '切換為深色模式'}</span>
    </button>
  )
}

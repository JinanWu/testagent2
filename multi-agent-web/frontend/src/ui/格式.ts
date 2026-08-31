/** 呈現層共用的格式化工具。後端時間一律為 Unix epoch 秒。 */

const 時間格式 = new Intl.DateTimeFormat('zh-TW', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** 把 epoch 秒轉成當地時區的可讀字串；無法解析時回傳固定文案。 */
export function 格式化時間(秒: number | null | undefined): string {
  if (typeof 秒 !== 'number' || !Number.isFinite(秒)) return '無資料'
  const 日期 = new Date(秒 * 1000)
  if (!Number.isFinite(日期.getTime())) return '時間不可顯示'
  return 時間格式.format(日期)
}

/** 把 epoch 秒轉成相對時間；超過一週改回絕對時間。 */
export function 格式化相對時間(秒: number | null | undefined): string {
  if (typeof 秒 !== 'number' || !Number.isFinite(秒)) return '無資料'
  const 差 = Date.now() / 1000 - 秒
  if (差 < 0 || 差 > 604_800) return 格式化時間(秒)
  if (差 < 60) return '剛剛'
  if (差 < 3600) return `${Math.floor(差 / 60)} 分鐘前`
  if (差 < 86_400) return `${Math.floor(差 / 3600)} 小時前`
  return `${Math.floor(差 / 86_400)} 天前`
}

/** 端點與憑證狀態的中文標籤；未知值原樣呈現。 */
export function 狀態文字(狀態: string): string {
  const 對照: Record<string, string> = {
    active: '啟用中',
    disabled: '已停用',
    archived: '已封存',
    inactive: '未啟用',
    expired: '已過期',
    revoked: '已撤銷',
    pending: '等待中',
    running: '執行中',
    succeeded: '成功',
    failed: '失敗',
    rate_limited: '流量限制',
    invalid_api_key: 'API Key 無效',
  }
  return 對照[狀態] ?? 狀態
}

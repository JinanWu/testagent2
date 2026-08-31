import type { DraftReceipt } from '../../api/endpoints'
import 圖示 from '../../ui/圖示'

export default function RateLimitConfirmation({ rateLimit }: { rateLimit: DraftReceipt['preview']['rateLimit'] }) {
  return (
    <section
      aria-labelledby="rate-limit-title"
      data-preview-field={true}
      className="rounded-lg border border-outline-variant bg-surface-container-low p-md"
    >
      <h3
        id="rate-limit-title"
        className="mb-sm flex items-center gap-sm font-headline-sm text-headline-sm text-on-surface"
      >
        <span aria-hidden={true} className="text-on-surface-variant">
          <圖示 名稱="流量" 大小={18} />
        </span>
        Rate limit（唯讀）
      </h3>
      <pre className="程式碼區塊">
        {JSON.stringify(
          {
            endpoint_per_minute: rateLimit.endpointPerMinute,
            credential_per_minute: rateLimit.credentialPerMinute,
          },
          null,
          2,
        )}
      </pre>
    </section>
  )
}

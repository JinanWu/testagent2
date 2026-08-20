import type { DraftReceipt } from '../../api/endpoints'

export default function RateLimitConfirmation({ rateLimit }: { rateLimit: DraftReceipt['preview']['rateLimit'] }) {
  return (
    <section aria-labelledby="rate-limit-title" data-preview-field={true}>
      <h3 id="rate-limit-title">Rate limit（唯讀）</h3>
      <pre>{JSON.stringify({
        endpoint_per_minute: rateLimit.endpointPerMinute,
        credential_per_minute: rateLimit.credentialPerMinute,
      }, null, 2)}</pre>
    </section>
  )
}

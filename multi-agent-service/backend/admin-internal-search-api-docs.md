# admin-internal-search API — API 交付文件

## 基本資訊

- **API 名稱**：admin-internal-search API
- **Endpoint URL**：`${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke`
- **API key**：`pk_SdYsZbQJ1Ozbgs942Wu907hEyYWWiocptvPGGGeR6c4`
- **Endpoint ID**：`endpoint-78db21eafe5f4a5b8901d772f00fc983`
- **版本**：v1
- **狀態**：啟用中

## 實際呼叫

```http
POST ${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke
Authorization: Bearer pk_SdYsZbQJ1…R6c4
Content-Type: application/json
```

## 送出請求格式

```json
{
  "input": {},
  "session_id": null,
  "metadata": {
    "endpoint_id": "endpoint-78db21eafe5f4a5b8901d772f00fc983"
  }
}
```

> 頂層只接受 `input`、`session_id`、`metadata` 三個欄位。

## Request schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "input"
  ],
  "properties": {
    "input": {},
    "session_id": {
      "anyOf": [
        {
          "type": "string",
          "maxLength": 128
        },
        {
          "type": "null"
        }
      ],
      "x-utf8-max-bytes": 128,
      "description": "Optional Published session identifier；上限 128 UTF-8 bytes。"
    },
    "metadata": {
      "anyOf": [
        {
          "type": "object"
        },
        {
          "type": "null"
        }
      ]
    }
  }
}
```

## Response schema

```json
{
  "additionalProperties": false,
  "properties": {
    "answer": {
      "type": "string"
    }
  },
  "required": [
    "answer"
  ],
  "type": "object"
}
```

## 多輪對話延續（session_id）

開新對話時 `session_id` 送 `null`；如果回應中帶回新的 `session_id`，同一段對話接下來每次都帶同一個值即可。

重新開始一段新對話時，再送一次 `null`。

## cURL 範例

```bash
curl -X POST '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke' -H 'Authorization: Bearer pk_SdYsZbQJ1…R6c4' -H 'Content-Type: application/json' --data '{"input":{},"session_id":"${SESSION_ID}","metadata":{"endpoint_id":"${ENDPOINT_ID}"}}'
```

## Python 範例

```python
import json
import urllib.request
url = '${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke'
payload = {'input': {}, 'session_id': '${SESSION_ID}', 'metadata': {'endpoint_id': '${ENDPOINT_ID}'}}
request = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Authorization': 'Bearer pk_SdYsZbQJ1…R6c4', 'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(request) as response:
    print(response.read().decode('utf-8'))
```

## 錯誤碼一覽

| HTTP | code | 說明 |
| ---- | ---- | ---- |
| 404 | `endpoint_not_found` | 找不到 endpoint slug。 |
| 401 | `invalid_api_key` | API key 無效。 |
| 401 | `api_key_expired` | API key 已過期。 |
| 403 | `endpoint_disabled` | Endpoint 已停用。 |
| 410 | `endpoint_archived` | Endpoint 已封存。 |
| 422 | `input_schema_invalid` | Input 不符合 schema。 |
| 502 | `model_output_schema_invalid` | 模型輸出不符合 response schema。 |
| 429 | `rate_limit_exceeded` | 呼叫頻率超過限制。 |
| 504 | `model_timeout` | 模型供應商逾時。 |
| 502 | `tool_execution_failed` | 工具執行失敗。 |
| 504 | `tool_timeout` | 工具執行逾時。 |
| 500 | `endpoint_misconfigured` | Endpoint 設定錯誤。 |
| 500 | `internal_error` | 伺服器內部錯誤。 |

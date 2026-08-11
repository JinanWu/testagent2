# Acceptance #5 — Service Account Create 驗收證據

> 範圍：只驗證 `03-Acceptance-05-Service-Account-Create.md`。
> 安全：本文不保存任何真實 secret；一次性金鑰一律以 `[REDACTED]` 表示。

## Checkpoint

- Base branch：`開發中主線`
- Base HEAD：`52e8d3af2de871c06dd2f90c29673110f038bfee`
- Base tree：`1dfc5caf5a51c9f501eda736e2633a7109624d2b`
- Parent branch：`Acceptance-05-Service-Account-Create`
- SA-1 commit：`d9146dbccf772dc45b00c80d2096610ae9155b13`
- SA-2 commit：`2ec5b4695a0624710b2173a63d407a1c8950fbd7`
- SA-3 commit：`2a731f13a8c5454eaa842ae627708390270c57c7`
- SA-4 commit：`4b25a47c2833999d7117e3bfa2c109cff08c7d24`
- Push：未執行。

## 範圍與實作判定

`開發中主線` 已包含完成的 Acceptance #4 production composition，因此本卡沒有重寫 production source。Acceptance #5 新增獨立的 canonical acceptance test，經公開 HTTP／OpenAPI seam 與既有 transaction/runtime seam，閉合下列產品證據：

1. canonical OpenAPI 有且只有一個 `POST /api/published-endpoints`；沒有 Service Account CRUD。
2. request 只有 `draft_id`、`slug`、`configuration_confirmation`，拒絕 `service_account_id`／owner／role claim。
3. 201 DTO 不揭露 `service_account_id`。
4. startup 的 publication coordinator 重用同一 Draft Aggregate、Owner Resolver、Tool Registry 與 Bundle Coordinator；shutdown 後 fail closed。
5. 真 Login → CSRF → Draft → Create 建立完整 SQLite graph；第二個 endpoint 取得不同 SA。
6. 一次性 API key `[REDACTED]` 不落 Published DB、bundle files 或 logs。
7. Create → shutdown → 刪除 owner live skill → fresh canonical app，仍由 exact SA/current-v1 Published snapshot 完成 invoke；provider 看不到 owner 密碼或 live skill root。
8. 既有 transaction、failpoint、concurrency、runtime mismatch 與 owner/global/workdir fallback tests 全數保留並通過。

### SA-1 RED 說明

原研究卡的 RED 前提是 canonical app 尚未接入 Endpoint Create；但使用者指定的新基底 `開發中主線@52e8d3a` 已先合入 Acceptance #4，因此新增的 SA-1 contract test在此基底直接 GREEN。沒有偽造 RED 紀錄，也沒有回退到舊 `27d3d71` 基底。

## 新增測試

`tests/發布介面/test_Acceptance05_服務帳戶建立Live.py`

- `test_canonical_OpenAPI只有一個endpoint_create且不接受service_account_id`
- `test_startup重用A3資源並於shutdown撤銷服務帳戶建立authority`
- `test_live登入草稿建立兩端點並產生不同服務帳戶且拒絕client_claim`
- `test_restart後由exact服務帳戶與v1快照完成invoke且不讀live_skill`

## 實際驗證

Hermes 執行環境會注入另一套 Python 3.11 `PYTHONPATH`；所有 repo 測試均明確使用 repo Python 3.12 venv 並移除該外部注入：

```bash
env -u PYTHONPATH AIAGENT_MODEL_MODE=fake .venv/bin/python -m pytest -q \
  tests/發布介面/test_端點發布交易.py \
  tests/發布介面/test_CP4_規劃發布安全路由.py \
  tests/發布介面/test_CP4_發布管理協調器.py \
  tests/發布介面/test_發布執行期隔離.py \
  tests/發布介面/test_CP4_Controller生產呼叫.py \
  tests/發布介面/test_Acceptance05_服務帳戶建立Live.py
```

結果：**245 passed**；0 failed。既存 Pydantic `UnsupportedFieldAttributeWarning` 3 筆。

```bash
env -u PYTHONPATH AIAGENT_MODEL_MODE=fake .venv/bin/python -m pytest -q
```

結果：**3914 passed、3 skipped**；0 failed。Warnings 為既存 Pydantic alias、Starlette per-request cookies 與 `on_startup/on_shutdown` deprecation。

```bash
env -u PYTHONPATH .venv/bin/python -m compileall -q 繁中代理
git diff --check 開發中主線...HEAD
```

結果：兩者 exit 0。

## 驗收結論

- Acceptance #4 live route dependency：已由 base checkpoint `52e8d3a` 關閉。
- Acceptance #5 canonical route／HTTP／SQLite graph／restart runtime：GREEN。
- Production source：零修改；沿用已合入的深模組與既有 seam，避免重複 composition。
- Repo 外 scope：零修改。
- Ledger：repo 內沒有 `First-Version-Acceptance-Ledger-20260719.md`，因此未修改外部規劃檔；本證據只記錄本卡 closure。
- 最終判定：**IMPLEMENTED（等待本分支 review 與合併 checkpoint）**。

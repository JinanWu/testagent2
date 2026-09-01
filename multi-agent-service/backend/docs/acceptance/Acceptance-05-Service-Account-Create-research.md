# Acceptance #5 — Service Account Create：現有 seam、#4 相依與最小實作路徑研究

> 研究範圍：只涵蓋 `03-Acceptance-05-Service-Account-Create.md`。
> 研究來源 Repo：`/Users/wujinan/Documents/ai平台開發中/testagent2`
> 收錄分支：`Acceptance-05-Service-Account-Create`
> 研究 HEAD：`19f0d3a16fd4ac3fffd9793e44517aa04af3e940`（branch `A4-Endpoint-Create`；merge commit 訊息 `merge: 完成 Acceptance 4 Endpoint Create`）
> Tree：`1dfc5caf5a51c9f501eda736e2633a7109624d2b`
> 結論：**Acceptance #4 在目前 branch 已閉合；#5 所需 production path 也已被 #4 實作接通。#5 的最小剩餘工作應是補一個 Acceptance #5 專用的 closure test／證據檔，不應再改 production composition。**

## 1. Canonical app composition

Canonical factory 仍是 `建立CP4ASGI應用程式(設定, Published設定)`；它驗證兩份 exact 設定後，把 `生產Controller建構器(Published設定)` 交給共用 `建立生產應用程式()`。這不是測試手工 app，也沒有替代 route composition。來源：

- `繁中代理/發布介面/asgi.py:58-73`
- `繁中代理/發布介面/生產組裝.py:59-100,103-120`

共用 composition 先在 `建立生產相依項()` 建立 canonical current-session 與 single-use CSRF dependencies，再把同一 callable identities 傳給 builder；auth router、附加 routers 與 lifespan factories 最後合成同一 `發布介面相依項`。來源：`繁中代理/發布介面/生產組裝.py:78-100`。

`生產Controller建構器` 按 Web→Published 組裝 routes/resources，startup 前檢查 Web DB 與 Published DB identity 不可相同，routes 直接串接為 `網頁.路由器清單 + 發布.路由器清單`。來源：`繁中代理/發布介面/生產Published執行.py:532-588`。

**判定：canonical app seam 已存在且已接通；#5 不需要改 `asgi.py`。**

## 2. Management builder / lifespan seam

`生產Published執行建構器` 在 app construction 建立三個 per-app lazy proxies（invoke、draft、management），不做 DB／FS I/O。當 `Published生產設定.憑證封套工廠` 存在時，它註冊 `建立安全規劃發布路由器(...)`，把同一 `延遲發布管理服務` 與 canonical session／CSRF dependencies 捕捉進 route；resource factory 則延後到 lifespan startup 呼叫 `_建立Published資源(...)`。來源：

- `繁中代理/發布介面/生產Published執行.py:455-531`
- `繁中代理/發布介面/生產Published管理.py:206-325`（management lazy proxy、lease、drain）

Startup 的 `_建立Published資源()` 會：

1. 驗證 Web／Published DB 隔離並 migration；
2. 建立 current-version resolver、credential verifier、snapshot repository、bundle loader、tool/model registries 與 invoke bridge；
3. 建立唯一 Planner resource，重用其 exact Draft Aggregate、Owner Resolver 與 Tool Registry；
4. 建立 `技能套件發布器`、`SQLite端點發布服務`（含 `service-account-{uuid}` generator）、`AESGCM憑證封套`、`發布管理協調器`；
5. 最後才把 coordinator 安裝進 management proxy。

來源：`繁中代理/發布介面/生產Published執行.py:697-809`，特別是 `755-802`。

Shutdown 先撤銷 management authority 並 drain active leases，再清除 credential／P04／bundle references，之後才關 Planner 與 invoke resources。來源：`繁中代理/發布介面/生產Published執行.py:384-454`。

**判定：原 spec 建議新增的「production management builder」已由 #4 落在既有 `生產Published執行.py` + `生產Published管理.py`，不應為 #5 再建第二套。**

## 3. Auth / CSRF seam

Canonical composition 建立並注入同一 current-session 與 CSRF dependency：`繁中代理/發布介面/生產組裝.py:78-95`。

Create handler 同時依賴兩者，先 strict parse body，再以 `_重建網頁身份()` 要求兩個回傳值都是 exact `網頁使用者` 且 principal ID 完全相同；不一致固定為 `500 identity_contract_invalid`，之後才進 threadpool 呼叫 management service。來源：

- `繁中代理/發布介面/路由/規劃發布.py:520-532`
- `繁中代理/發布介面/路由/規劃發布.py:596-613`

成功回傳自行建立的 `JSONResponse` 時，`_傳遞CSRF接續()` 會把 dependency 寫入 injected response 的 successor header/cookie 複製到真正 response，維持 single-use CSRF chain。來源：`繁中代理/發布介面/路由/規劃發布.py:706-726`。

現有 canonical test 也直接比較 Draft/Create route dependencies 與 `/api/auth/me` dependency identity，並驗證第二個 dependency 是模組 CSRF dependency。來源：`tests/發布介面/test_Acceptance04_端點建立Live.py:578-658`。

## 4. Route coordinator seam

正式 management router 已包含：

- `POST /api/published-endpoints/draft`
- `POST /api/published-endpoints`
- `POST /api/published-endpoints/{endpoint_id}/versions`

來源：`繁中代理/發布介面/路由/規劃發布.py:538-593`；其基礎 Create handler 位於同檔 `520-532`。

Create 的 service adapter `_安全發布端點()` 只從 authenticated principal 與三鍵 request 建立 `發布確認`，呼叫一次 `原子發布`，public 201 DTO 僅含 endpoint/version/status/initial key，不含 `service_account_id`。來源：`繁中代理/發布介面/路由/規劃發布.py:616-642`。

`發布管理協調器.原子發布()` 在任何 graph write 前依序讀 authoritative Draft、解析 owner 能力、確認 server-visible values、產生 IDs/entropy、AES-GCM envelope、發布 bundle，然後把 owner、snapshot、credential、IDs、receipt 與 transaction-time authority callback 一次交給 P04。來源：`繁中代理/發布介面/規劃/發布管理.py:98-175`。

P04 在 `BEGIN IMMEDIATE` 內先執行二次 owner authority callback，再依 SA→Endpoint→Version→Draft consumption→Metadata→Credential→Bundle receipt→Audit→Current pointer 寫入；SA row 與所有 graph rows屬同一 SQLite transaction。來源：`繁中代理/發布介面/規劃/端點發布.py:559-582,592-670`。Schema 以 endpoint 的 `service_account_id TEXT NOT NULL UNIQUE REFERENCES service_accounts(id)` 保證一對一。來源：`繁中代理/發布介面/遷移/0001_建立發布端點核心.sql:1-17`。

## 5. Runtime restart / readback seam

Startup 每次重建 `SQLite目前版本解析器`、`SQLite發布快照儲存庫`、bundle loader 與 runtime bridge，因此 restart 不依賴前一 app 的記憶體 coordinator。來源：`繁中代理/發布介面/生產Published執行.py:719-753`。

Invocation bridge 從 pinned request 取得 endpoint/service-account/version 三個 IDs，以 exact version 查 snapshot，並要求三者與 snapshot 相同，然後把 snapshot 中的 SA ID 與 version ID交給 executor。來源：`繁中代理/發布介面/執行期/呼叫橋接.py:117-139,177-198`。

Snapshot repository 的單一 JOIN row 同時投影 version 與 DB-pinned SA；`載入服務帳戶上下文()` 只接受 source=`endpoint_version_snapshot`，且傳入 SA 必須等於該 row 的 SA。來源：`繁中代理/發布介面/執行期/快照儲存庫.py:85-133,156-193`。

Executor 依固定 version→SA→bundle→tool→model 順序組裝，先重建 exact snapshot，再呼叫 `載入服務帳戶上下文或失敗關閉()`，並驗證 snapshot/context cross-fields；任何 mismatch 在 bundle/tool/model 前失敗。來源：`繁中代理/發布介面/執行期/執行器.py:900-959`。

`ServiceAccountContext` 是 frozen、slots、無 owner/session/memory/workdir 欄位的 DTO；loader wrapper 唯一允許 `endpoint_version_snapshot` source，拒絕 fallback。來源：`繁中代理/發布介面/執行期/服務帳戶.py:20-38,89-135`。

Canonical restart E2E 已走 Create→shutdown→刪除 source skill→第二個 canonical app→Invoke 200，證明 runtime 從 durable v1 bundle/snapshot 重建。來源：`tests/發布介面/test_Acceptance04_端點建立Live.py:1503-1553`。另有五種 restart tamper/missing-pin fail-closed 測試：同檔 `1556-1636`。

## 6. Acceptance #4 是否已閉合？

**目前 branch 上已閉合。** 證據不是只看 commit 訊息：

- HEAD `19f0d3a` 是 merge commit `merge: 完成 Acceptance 4 Endpoint Create`。
- Canonical test 明言只使用 `建立CP4ASGI應用程式`，不建手工 FastAPI app：`tests/發布介面/test_Acceptance04_端點建立Live.py:1-6`。
- OpenAPI、exact request/response schema、canonical dependency identities：同檔 `578-658`。
- 真 Login→Draft→Create→八張 graph tables readback，包含 endpoint→SA 一對一 FK 與 audit metadata：同檔 `1018-1128`。
- Canonical restart invoke：同檔 `1503-1553`。
- 代表性 Bundle／SQLite failure 的 HTTP 500 與零 active publication：同檔 `1388-1435`。

本研究實跑：

```bash
env -u PYTHONPATH AIAGENT_MODEL_MODE=fake .venv/bin/python -m pytest -q \
  tests/發布介面/test_Acceptance04_端點建立Live.py \
  tests/發布介面/test_發布執行期隔離.py \
  tests/發布介面/test_端點發布交易.py
```

結果：**245 passed**，0 failed；2 個既存 Pydantic `UnsupportedFieldAttributeWarning`。

注意：Hermes shell 注入的 `PYTHONPATH` 指向 Python 3.11 site-packages，直接執行 repo Python 3.12 會錯載 native extensions（`pydantic_core`／`rpds` collection error）；移除 `PYTHONPATH` 後 repo `.venv` 正常。這是執行環境污染，不是 product failure。

若判定「閉合」要求已進 default integration branch，仍需由 integration owner確認 `A4-Endpoint-Create` 是否已 merge 到該目標；本研究只能確認目前指定 worktree/branch 的 source 與 tests。

## 7. 可直接重用的 fixtures / tests

優先直接重用 `tests/發布介面/test_Acceptance04_端點建立Live.py` 的 helpers，不要複製第二套 composition：

- `_安裝固定工具()`：固定 tool release（`64-86`）
- `_建立憑證封套()`：真 AES-GCM envelope（`104-112`）
- `_建立正式應用程式()`：canonical CP4 app fixture helper（`131-172`）
- `_建立Owner技能與使用者()`：真 Web owner authority（`272-296`）
- `_登入Owner()`、`_建立Server草稿()`、`_建立Server確認()`、`_建立Endpoint()`（`299-367`）
- `_建立完整副作用快照()`：全部 Published tables + bundle tree readback（`442-482`）

可直接作 #5 regression/closure 的現有 tests：

1. `test_canonical_OpenAPI包含唯一draft與endpoint_create`（route/schema/no-SA-public-field）
2. `test_正式端點建立拒絕客戶端權威聲稱且零發布副作用`（含 `service_account_id` 422）
3. `test_真Login草稿建立Endpoint成功並readback完整圖形與一次性秘密`（SA row/FK/八表 graph）
4. `test_已消耗CSRF重放Create固定403且management完全不進入`
5. `test_代表性Bundle或SQLite失敗經canonical_Create固定500且零active_publication`
6. `test_Create成功後來源刪除且重啟仍由v1_bundle完成Invoke`
7. `test_Canonical_Create後重啟竄改或缺runtime_pin皆在模型呼叫前拒絕`
8. `tests/發布介面/test_端點發布交易.py` 的 statement failpoints、commit/rollback/close、duplicate IDs、same-slug concurrency tests（例如 `460`, `506`, `523`, `735`, `965`, `1143` 起）
9. `tests/發布介面/test_發布執行期隔離.py` 的 DTO/no-owner/fallback/mismatch tests（例如 `88`, `134`, `151`, `205`, `487` 起）

## 8. 最小實作路徑

目前 production 已具備 #5 的 route→coordinator→P04 SA row→restart runtime path。最小路徑應是：

1. **新增一個 A5 專用 test file**，只從 canonical app helper 建 app，作 Acceptance ownership／evidence 聚合；不要新增 route、builder、service 或 schema。
2. 用既有 true Login/CSRF/Draft/Create helper 建立 endpoint A，讀 Published DB 並 assert：恰一 SA、endpoint.SA FK 指向該 row、public DTO 無 SA ID。
3. 再建立 endpoint B（fresh Draft + successor CSRF 或 fresh login），assert `COUNT(DISTINCT service_account_id)=2`，補上目前尚未找到的「不同 endpoint 不共用 SA」live assertion。
4. 對其中一個 endpoint shutdown/restart/invoke；在 DB readback 保存 expected SA/version IDs，並透過既有 runtime pipeline驗證 exact pin。若要讓 A5 證據更直接，可在測試 seam 觀測 snapshot repository loader arguments，但不要修改 production API。
5. 重用 P04 transaction suite 與 runtime isolation suite；不要在 A5 live file 重寫所有 statement failpoints。
6. 跑 focused + full backend，記錄 branch/HEAD/status；不更新 production code。

只有測試發現實際缺口時才進 production 修改；依目前 source 與 245-pass 結果，預期不需要。

## 9. 最小 allowlist

**建議 allowlist（目前最小）：**

- 新增：`tests/發布介面/test_Acceptance05_服務帳戶建立Live.py`
- 新增／更新研究證據：`docs/acceptance/Acceptance-05-Service-Account-Create-research.md`

**Production allowlist：空集合。** 特別不應修改：

- `繁中代理/發布介面/asgi.py`
- `繁中代理/發布介面/生產Published執行.py`
- `繁中代理/發布介面/生產Published管理.py`
- `繁中代理/發布介面/路由/規劃發布.py`
- P04、runtime、migrations、bundle、credential primitives

若 A5 test 暴露真 bug，才以失敗證據縮小 allowlist；不得先按舊 spec 的建議重做已由 #4 完成的 composition。

## 10. 風險

1. **重複實作風險（最高）**：A5 spec 是基於舊 commit `27d3d713`，其中「canonical route missing」已被 #4 branch 消除；照舊卡新增 builder 會產生第二套 Draft/Owner/Tool Registry 或 duplicate route。
2. **證據歸屬風險**：#4 live test 已證明 SA graph，但缺 A5-named closure artifact，Ledger reviewer 可能仍判 PARTIAL；以薄 A5 test 聚合即可。
3. **尚缺明確 live assertion**：repo 搜尋未找到「兩個 endpoint 使用不同 SA」的 dedicated assertion。Schema `UNIQUE` 防止共享同一 SA，但 live test仍應直接建立兩個 endpoint驗證 generator/composition。
4. **Restart 證據間接性**：現有 canonical restart test確實經 production bridge/executor 載入 SA context，但測試主要斷言 HTTP 200/bundle/model；A5 closure最好附 expected SA/version DB readback，讓 reviewer 不必靠 control-flow 推論。
5. **跨資源 ACID 誤述**：SQLite graph 是單 transaction，bundle filesystem 不是；DB fail 後的 orphan handling 只能說 reconciliation/隔離，不能宣稱跨 FS+DB ACID。來源：coordinator exception handling `規劃/發布管理.py:176-207`。
6. **Secret boundary**：A5 test不可把 raw initial key寫進研究筆記、failure message、snapshot 或 persistent artifact；沿用現有 helper 在作用域內收斂並以 `[REDACTED]` 表述。
7. **Branch integration風險**：研究時 HEAD 在 `A4-Endpoint-Create`；本次 A5 實作已依使用者指定，從包含該成果的 `開發中主線@52e8d3a` 建立。若日後從舊 base 重建，仍會重新遇到 route missing。
8. **測試環境污染**：Hermes 的 `PYTHONPATH` 會讓 repo Python 3.12 載入 Hermes Python 3.11 native packages。測試命令需 `env -u PYTHONPATH ... .venv/bin/python`，否則 collection error 容易被誤判為產品回歸。

## 最終判定

- **#4 dependency：current branch CLOSED。**
- **#5 production capability：已由 #4 wiring 實際接通。**
- **#5 最小剩餘：tests/evidence-only closure，主要補「兩個 endpoint→兩個不同 SA」與更直接的 restart SA/version readback。**
- **不建議任何 production code change，除非新增 A5 test先出現可重現 RED。**

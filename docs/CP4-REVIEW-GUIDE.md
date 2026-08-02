# CP4 可讀歷史審查指南

## 目的

這組 `review/cp4-*` 分支把 CP3 到 CP4 安全檢查點的原始百餘個正式 commits，整理成 **9 個依賴相連的產品能力階段**。每一階段只保留一個語意明確的 commit，讓有限的審查人力能依產品流程閱讀，而不必先理解歷史上的重建、RED archive 或修正輪次。

這是**可讀投影**，不會改寫或取代原始已審歷史。每個可讀 commit 的完整 tree 都逐位元對應一個原始 GREEN milestone。

## 閱讀方式

請按順序審查；每個分支都建立在上一個分支之上，所以比較相鄰分支即可，不必重複閱讀前面內容。

| 順序 | 分支 | 先回答的問題 |
|---:|---|---|
| 00 | `review/cp4-00-cp3-base` | CP4開始前已經有哪些Production API與Web能力？ |
| 01 | `review/cp4-01-server-planner` | 如何讓伺服器而不是前端決定技能、工具與綱要？ |
| 02 | `review/cp4-02-immutable-skill-bundle` | 如何把技能內容安全發布成不可變版本？ |
| 03 | `review/cp4-03-version-pinned-runtime` | Runtime如何只讀取指定版本的Bundle、Tools與Snapshot？ |
| 04 | `review/cp4-04-stable-invocation-bridge` | Stable invoke如何接上限流、台帳、模型與工具回合？ |
| 05 | `review/cp4-05-production-composition` | Published服務如何安全啟動、失敗清理並避免阻塞？ |
| 06 | `review/cp4-06-initial-publish-and-recovery` | 首次發布如何原子建立資料，DB失敗後如何處理孤兒Bundle？ |
| 07 | `review/cp4-07-atomic-version-switch` | 建立v2時如何確保外部只看到完整舊版或完整新版？ |
| 08 | `review/cp4-08-owner-authority` | 發布前如何重新驗證Owner真正可用的技能與工具？ |
| 09 | `review/cp4-09-publish-coordinator` | 如何把權威重驗、Bundle、Snapshot、SQLite與一次性API key串成完整流程？ |

最終整合分支：`review/cp4-readable-integration`。

原始98個正式commits完整保留於：`review/cp4-original-checkpoint`。一般審查先讀可讀分支；只有需要追查設計演進、測試補強或來源SHA時，才回到原始分支。

## 建議審查順序

每個能力分支都建議依下列順序閱讀：

1. 先讀該commit message的「目的」與「審查順序」。
2. 先看production contract／DTO／service，再看route或composition。
3. 接著看成功路徑測試，建立正常流程心智模型。
4. 最後看敵對、並行、回滾與秘密清理測試。
5. 只有需要追查設計演進時，才回原始來源commit與checkpoint。

## 重要邊界

- 最終可讀整合樹必須與原CP4安全樹 `6556eceb84f29ff1c33219b155df997dbbc1ca5c` 完全相同。
- 不納入後續 `delivery/外部呼叫可靠性` 的dirty候選。
- 不包含任何 archive／RED 歷史。
- 不修改 `main`，也不聲稱這組分支已完成CP4尚未通過的Task 7～8最終Gate。
- 這些分支的用途是降低閱讀成本，不取代既有測試、Spec、Quality或Release Gate。

## 對照來源

| 可讀分支 | 原始GREEN來源 |
|---|---|
| `review/cp4-01-server-planner` | `bb8e10243d6bbc16a7eeab57e8c5a66202cf7582` |
| `review/cp4-02-immutable-skill-bundle` | `389f5f6829ff9e971bbee9981e7f06ae347e3e5b` |
| `review/cp4-03-version-pinned-runtime` | `067ea6f2b58ea5786e686af947bc09e89e89f799` |
| `review/cp4-04-stable-invocation-bridge` | `bbb65fc947d51e0e872cc44f68a48db4e0532ab8` |
| `review/cp4-05-production-composition` | `cfc7c08420e65e280b4d9dde0f025a0065078ce8` |
| `review/cp4-06-initial-publish-and-recovery` | `8acc7fc99d9e9aabd91ecde83810606f7204b6b1` |
| `review/cp4-07-atomic-version-switch` | `6f07862b4acccef79dd42ff46ed391fe7cd4d969` |
| `review/cp4-08-owner-authority` | `236c6ca7f7d0463ecd7dbabe9d36fdb34233927d` |
| `review/cp4-09-publish-coordinator` | `6556eceb84f29ff1c33219b155df997dbbc1ca5c` |

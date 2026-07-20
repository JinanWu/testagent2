# 已發布技能套件相容性

## 清單版本一

清單版本一維持既有 JSON 外形。`source_skills[].source_hash` 現在定義為該技能所
複製的 `SKILL.md` 之 SHA-256 摘要，並與規劃器釘選的
`content_sha256_reference` 相同。驗證器會拒絕把整個檔案列表摘要誤放在這個欄位的
舊清單。`bundle_hash` 仍是所有已複製檔案的集合摘要，因此整個套件的完整性語意
不變。

## 權限清單

權限協調器同時接受兩種精確鍵集合：舊版的
`permission_revision`、`skills`，以及新版再加入
`bundle_id`、`manifest_reference`、`manifest_digest`、`sha256` 的六鍵版本。
兩種版本都嚴格檢查純量型別、技能順序與每個技能的 SHA-256；六鍵版本另外要求
清單參照精確為 `<bundle_id>/manifest.json`，並要求兩個摘要都是小寫十六進位
SHA-256。額外鍵、缺鍵、錯誤型別、錯誤摘要或錯誤參照一律拒絕。

## 不可變驗證投影

不可變的清單驗證投影會以具型別、唯讀的 `source_skills` 項目公開來源技能。
發布協調只使用這個已驗證投影，不信任解碼後仍可變的 manifest JSON。

---
name: administrative
description: 透過搜尋受管理的 BigQuery 文件索引來回答管理部相關問題，提供帶引用、具版本意識的結果。
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [administrative, BigQuery, documents, hybrid-search, policy]
---

# 管理部

當使用者詢問管理部文件、政策、流程、表單、請假、公告、內部規章，或任何應以管理部文件索引為依據的問題時，使用此技能。

## 檢索

- 回答管理部事實性問題時，使用 `administrative_search` 工具。
- 一般問題預設使用 `search_mode="hybrid"`。
- 只有在使用者明確要求比對確切文字、標題、分類、來源檔案或版本時，才使用 `search_mode="keyword"`。
- 只有在使用者明確要求純語意搜尋，或在比較／除錯檢索行為時，才使用 `search_mode="semantic"`。
- 除非使用者明確要求調整語意／關鍵字比例，否則不要設定 `hybrid_weights`。預設為語意 0.65、關鍵字 0.35。
- 預設設定 `include_images=true`。

## 圖片

- 圖片是已由文字檢索命中之文件的補充脈絡。
- 不要把圖片檔名當作主要的搜尋途徑。
- 若回傳的圖片有助於說明某個流程、表單、流程圖或截圖，請簡短提及作為佐證資料。
- 若工具只回傳圖片的中繼資料，不要臆造圖片內容。

## 回答

- 事實性陳述須以工具回傳的結果為依據。
- 管理部答案須引用 `title`、`source_file`、`version` 與 `category`。
- 若結果包含多個版本，在可辨識時優先採用最新版本，並說明所採用的版本。
- 若結果彼此衝突，說明衝突之處並同時引用兩個來源。
- 若找不到相關結果，說明管理部文件索引未能提供足夠證據。不要臆測政策細節。
- 除非使用者另有要求，否則一律以繁體中文回答。

## 引用格式

在答案後附上精簡的來源註記：

```text
來源：
- title: ...
  source_file: ...
  version: ...
  category: ...
```

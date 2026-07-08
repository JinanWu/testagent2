---
name: skill-authoring
description: 建立 SKILL.md 的規範:skill 是給 LLM 讀的流程說明,不是 code。含格式、結構與禁忌。
---

# 如何建立技能(SKILL.md)

## 核心觀念(最重要)

Skill 是**給 LLM 讀的流程說明**,讀完照著做,用現有工具(web_search、terminal…)完成任務。

**Skill 不是 code、不是函式、不是 plugin 規格。** 沒有任何機制會執行 SKILL.md 裡的程式碼——寫進去的 `def`/`implementation` 只是死文字,永遠不會跑。

**絕不要放**:`parameters:`、`implementation:`、`def ...` 函式、呼叫/回傳規格。那是「工具定義」的長相,不是 skill。

## 檔案格式

必須是 YAML frontmatter + markdown 正文:

    ---
    name: <小寫-連字號,如 google-maps-reviews>
    description: <一句話:這個 skill 幫你做什麼>
    ---

    # 標題

    ## 何時用
    <什麼情況該用這個 skill>

    ## 步驟
    1. 用 <工具> 做 <事>
    2. ...

    ## 注意
    - <常見坑 / 邊界情況>

## 規則

- `name`:只能小寫字母、數字、`-`、`_`、`.`,以字母或數字開頭;每個使用者內唯一。
- frontmatter 必須包含 `name` 與 `description`,以 `---` 開頭、`---` 結尾。
- 正文寫「用哪個工具、照什麼步驟、要注意什麼」——像食譜,不像程式。

## 怎麼建立

用 `skill_manage(action='create', name='...', content='<完整 SKILL.md>')`。

建立前先自問:**我是在「記錄一個做法」還是在「設計一個函式」?** 應該永遠是前者。若你發現自己在寫函式簽名或實作,就是走偏了。

## 好範例(照抄這個結構)

    ---
    name: google-maps-reviews
    description: 查詢地點在 Google Maps 的評價數量,用 web_search 從搜尋結果解析。
    ---

    # 查 Google Maps 評價數

    ## 何時用
    使用者想知道某地點在 Google Maps 上有幾則評價。

    ## 步驟
    1. 用 web_search 搜尋 `Google Maps <地點> reviews`
    2. 從結果描述找「12,345 reviews」或「12,345 則評論」的數字
    3. 去掉千分位逗號後回報給使用者;找不到就直說找不到,不要編造

    ## 注意
    - web_search 結果不穩定,第一筆沒有就多看幾筆
    - 中英文字樣都要找(reviews / 則評論)

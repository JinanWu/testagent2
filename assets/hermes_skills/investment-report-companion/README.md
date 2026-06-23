# Investment Report Companion Skill Package

這是一個用來複製「互動式投資研究陪讀」體驗的 skill 套件。

它不是一般研究報告摘要工具。主要用途是：

- 建立公司的商業模型；
- 拆解獲利傳導鏈；
- 區分已實現獲利與 optionality；
- 質疑券商估值；
- 根據使用者的疑點局部深入；
- 保留漸進式對話節奏。

## 檔案結構

```text
investment-report-companion/
├── SKILL.md
├── README.md
├── framework/
│   ├── company-classification.md
│   ├── earnings-bridge.md
│   ├── valuation-playbook.md
│   ├── dialogue-policy.md
│   └── report-challenge-checklist.md
└── examples/
    ├── zhongxing-electric.md
    ├── evergreen-marine.md
    ├── global-unichip.md
    ├── kingding-expansion.md
    ├── fubon-financial.md
    ├── chicony-cooling.md
    └── honhai-ai-server.md
```

## 使用方式

將整個資料夾放入支援 skill / agent instruction 的環境。

建議把 `SKILL.md` 作為主要入口，並允許 agent 在分析不同公司時讀取 `framework/` 與 `examples/`。

## 設計原則

這個套件刻意避免：

- 機械式逐段摘要；
- 第一次回答就塞入所有資訊；
- 無條件接受券商目標價；
- 把潛在商機當成已實現獲利；
- 對所有公司套用同一種估值方法。

它鼓勵：

- 先分類公司；
- 再建立獲利橋接；
- 再挑戰估值；
- 最後沿著使用者問題深入。

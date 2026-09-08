# Cloud Run Sandboxes 說明

> 一句話：讓 agent 的 `terminal` 工具在隔離環境裡執行指令，碰不到我們的機密與原始碼。

狀態：Google 官方功能，Public Preview（pre-GA）。

---

## 為什麼要做

`terminal` 是完整 shell。給了使用者，他就能讀走 `dev.env` 憑證、改掉專案原始碼、反打內部服務。

`allowed_workdirs` 是 app 層檢查，**擋不住絕對路徑**。真正的邊界只能靠 OS，Cloud Run Sandboxes 就是 Google 做好的現成方案。

## 擋住什麼

| 邊界 | 預設行為 |
|------|---------|
| 機密／環境變數 | 沙箱**讀不到**父服務的環境變數、secrets、雲端 metadata |
| 檔案系統 | 對 host 容器**唯讀** |
| 網路 | 對外流量預設全擋 |
| 權限 | 非 root |
| 生命週期 | 用完即銷毀 |

---

## 怎麼部署

### 1. Dockerfile 不用改

沙箱執行檔 `/usr/local/gcp/bin/sandbox` 是 **Cloud Run runtime 自動注入**的，不在 image 裡，也裝不進去。

### 2. 部署時加 `--sandbox-launcher`

```bash
# 打包
docker build -t asia-east1-docker.pkg.dev/lab-cola-rd/testagent2/testagent2:v1 \
  multi-agent-service/backend

# 推送
docker push asia-east1-docker.pkg.dev/lab-cola-rd/testagent2/testagent2:v1

# 部署（沙箱開關在這一步）
gcloud beta run deploy testagent2 \
  --image asia-east1-docker.pkg.dev/lab-cola-rd/testagent2/testagent2:v1 \
  --sandbox-launcher \
  --update-env-vars TERMINAL_SANDBOX_EGRESS=on
```

既有服務只想開沙箱、不換 image：

```bash
gcloud beta run services update testagent2 --sandbox-launcher
```

關掉：

```bash
gcloud beta run services update testagent2 --no-sandbox-launcher
```

> ⚠️ 環境變數一律用 `--update-env-vars`。`--set-env-vars` 會**先清空所有既有環境變數**，服務會因為缺 `TESTAGENT2_WEB_ORIGINS` 等必填值而啟動失敗。

### 宣告式寫法（改用 YAML 部署時）

`sandboxLauncher` 掛在 container 層級，跟 `image` 同一階：

```yaml
metadata:
  annotations:
    run.googleapis.com/launch-stage: BETA
spec:
  template:
    spec:
      containers:
      - image: ...
        sandboxLauncher: true
```

---

## 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `TERMINAL_SANDBOX` | 未設定 | `on` 強制進沙箱；`off` 強制直接執行；**未設定時在 Cloud Run 上自動啟用**（靠 `K_SERVICE` 判斷），地端則直接執行 |
| `TERMINAL_SANDBOX_EGRESS` | `off` | `on` 才允許沙箱對外連線。關著時 `pip install`／`curl`／`git clone` 都會失敗 |
| `TERMINAL_SANDBOX_STATE_DIR` | `/tmp/terminal-sandbox-state` | 工作階段狀態檔存放位置 |

部署到 Cloud Run **只有 `--sandbox-launcher` 是必要的**，`TERMINAL_SANDBOX` 平常不用設——它是給「地端想測沙箱」或「雲端要緊急關掉」用的逃生口。

---

## 程式在哪

| 檔案 | 做什麼 |
|------|--------|
| `繁中代理/沙箱執行.py` | 決定要不要進沙箱、組出沙箱 argv |
| `繁中代理/基本工具.py` | `執行終端指令` 呼叫上面那支 |
| `tests/test_沙箱執行.py` | 24 個測試 |

指令會被包成：

```
sandbox do --write [--sync-tar=<狀態檔>] [--allow-egress] -- /usr/bin/bash -c "<指令>"
```

### 兩個設計決定

**fail closed**：在 Cloud Run 上如果沙箱不可用，直接拋 `沙箱不可用`，**不會退回主機裸跑**。靜默降級會讓人以為指令被隔離了，比明確失敗危險得多。

**工作階段內檔案延續**：`sandbox do` 本身用完即刪，若不處理，上一輪 `mkdir` 的目錄下一輪就不見了。所以同一個 session 共用一個 `--sync-tar` 狀態檔，進去解包、出來打包。（跑著的行程不會延續，但 terminal 本來就不支援背景行程。）

---

## 注意事項

- **CPU／記憶體是共用的**。沙箱跟 host container 共享 `--cpu`／`--memory`，開了之後要確認額度夠同時養 app 和數個沙箱。
- **開了 egress 就少一道防線**。沙箱裡的指令可以對外送資料。目前是整個服務一起開關，做不到「爬蟲能連網、一般 terminal 不能」——要做到得改程式（`建立沙箱指令` 已留 `允許連線` 參數，但 `執行終端指令` 沒傳進去）。
- **pre-GA**，指令要用 `gcloud beta`，且隨時可能變動。先內部試點，別一開始就壓大量正式流量。
- 開啟後服務會自動跑在**第二代執行環境**。

## 建議

1. **一般使用者（數百名）**：本來就不給 `terminal`。查行程／比機票用不到，風險直接歸零。
2. **內部／爬蟲需求**：走沙箱。

---

## 參考

- [Code execution in Cloud Run](https://docs.cloud.google.com/run/docs/code-execution)
- [Configure sandboxes for services](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes)
- [Google Cloud Run sandboxes are in public preview（Blog）](https://cloud.google.com/blog/topics/developers-practitioners/google-cloud-run-sandboxes-are-in-public-preview)

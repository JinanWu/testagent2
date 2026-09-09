"""terminal 工具的 Cloud Run Sandboxes 執行層。

功能：
    集中「terminal 指令要不要進沙箱、怎麼進」的決定。`基本工具.執行終端指令`
    只問這裡要用哪一組 argv，不自己判斷環境。

    沙箱由 Google 提供，在容器內是 `/usr/local/gcp/bin/sandbox`，語法為：

        sandbox do [選項] -- <指令> [參數]

    沙箱提供三層隔離：讀不到父容器的環境變數與 secrets、對 host 檔案系統唯讀、
    對外網路預設全擋。前兩項是本專案最主要的目的——擋住「用絕對路徑讀
    dev.env」這類 `allowed_workdirs` 擋不掉的路徑。

工作階段內的檔案延續：
    `sandbox do` 是「建立→執行→刪除」，本身用完即刪。若不處理，模型上一輪
    `mkdir 工作區`、下一輪要寫檔案時那個目錄已經不存在了——這與 terminal 既有
    行為（檔案寫在容器上會留著）不符。

    因此同一個工作階段共用一個 `--sync-tar` 狀態檔：進沙箱前解包、離開時打包，
    檔案狀態就能跨呼叫延續。注意跑著的行程不會延續（起一個 server 後它會隨沙箱
    消失）——但 terminal 既有實作也不支援背景行程，所以沒有退步。真的需要常駐
    行程時，官方做法是改用 `sandbox run --detach` + `sandbox exec`。

環境變數：
    TERMINAL_SANDBOX: on / off。**未設定時依環境決定**——在 Cloud Run 上一律
        要求沙箱（fail closed），地端才允許直接執行。這樣「忘記在雲端設定」會
        變成明確錯誤，而不是靜默裸跑。
    TERMINAL_SANDBOX_EGRESS: on 表示允許沙箱對外連線。預設 off，因此
        pip install／npm install／git clone／curl 這類需要網路的指令會失敗，
        需要時才明確打開。
    TERMINAL_SANDBOX_STATE_DIR: 工作階段狀態檔存放目錄，預設
        /tmp/terminal-sandbox-state。
"""

from __future__ import annotations

import hashlib
import os
import shlex
from pathlib import Path

from .環境設定 import 載入本機環境檔

沙箱可執行檔 = Path("/usr/local/gcp/bin/sandbox")

# 沙箱內用來承接完整 shell 指令的直譯器。terminal 的既有語意是「一整行 shell」，
# 含管線與 &&，所以不能直接把字串當 argv 丟進去。
沙箱內Shell = "/usr/bin/bash"

預設狀態目錄 = Path("/tmp/terminal-sandbox-state")

_肯定值 = {"on", "1", "true", "yes"}
_否定值 = {"off", "0", "false", "no"}


class 沙箱不可用(RuntimeError):
    """要求進沙箱但環境不具備沙箱能力。

    刻意讓它往外拋而不是默默退回直接執行：靜默降級會讓呼叫端以為指令被隔離了，
    實際上卻在主機上裸跑，比明確失敗危險得多。
    """


def _讀取開關(名稱: str) -> bool:
    """讀取一個 on/off 環境開關。

    參數：
        名稱: 環境變數名稱。
    返回值：bool。值為 on/1/true/yes 時為 True。
    """
    載入本機環境檔()
    return (os.getenv(名稱) or "").strip().lower() in _肯定值


def 在雲端執行() -> bool:
    """目前是否跑在 Cloud Run 上。

    參數：無。
    返回值：bool。

    以 Cloud Run runtime 一定會注入的 `K_SERVICE` 判斷。容器契約保證這個變數
    存在，且它不是我們自己設的，所以不會因為漏設定而誤判成地端。
    """
    return bool((os.getenv("K_SERVICE") or "").strip())


def 沙箱是否啟用() -> bool:
    """terminal 是否應該把指令送進沙箱。

    參數：無。
    返回值：bool。

    三態決定：
        明確 on  → 一律進沙箱。
        明確 off → 一律直接執行（唯一的逃生口，必須是刻意設定的）。
        未設定   → 在 Cloud Run 上進沙箱，地端直接執行。

    未設定時偏向沙箱是刻意的：正式環境漏設環境變數不該退化成「完整 shell 直接
    在主機上跑」。漏設會讓 terminal 明確報錯（沙箱不可用），而不是安靜地失去隔離。
    """
    載入本機環境檔()
    值 = (os.getenv("TERMINAL_SANDBOX") or "").strip().lower()
    if 值 in _肯定值:
        return True
    if 值 in _否定值:
        return False
    return 在雲端執行()


def 沙箱是否允許連線() -> bool:
    """沙箱內的指令是否可以對外連線。

    參數：無。
    返回值：bool。預設 False。
    """
    return _讀取開關("TERMINAL_SANDBOX_EGRESS")


def 沙箱是否可執行() -> bool:
    """容器內是否真的有可執行的沙箱工具。

    參數：無。
    返回值：bool。
    """
    return 沙箱可執行檔.is_file() and os.access(沙箱可執行檔, os.X_OK)


def 取得狀態目錄() -> Path:
    """工作階段狀態檔的存放目錄。

    參數：無。
    返回值：Path。
    """
    載入本機環境檔()
    設定值 = (os.getenv("TERMINAL_SANDBOX_STATE_DIR") or "").strip()
    return Path(設定值) if 設定值 else 預設狀態目錄


def 工作階段狀態檔(工作階段識別碼: str | None) -> Path | None:
    """回傳該工作階段的沙箱狀態 tar 路徑。

    參數：
        工作階段識別碼: 目前 session id；None 表示沒有可延續的對象。
    返回值：
        Path | None。None 表示這次呼叫不做狀態延續。
    副作用：確保狀態目錄存在。

    檔名用 session id 的 sha256 而不是原字串：session id 來自上層，這裡不該假設
    它只含檔名安全字元，雜湊後永遠是固定長度的十六進位，不會有路徑穿越問題。
    """
    if not 工作階段識別碼:
        return None
    目錄 = 取得狀態目錄()
    目錄.mkdir(parents=True, exist_ok=True)
    代號 = hashlib.sha256(工作階段識別碼.encode("utf-8")).hexdigest()[:32]
    return 目錄 / f"{代號}.tar"


def 建立沙箱指令(
    指令: str,
    工作目錄: str | None = None,
    允許連線: bool | None = None,
    工作階段識別碼: str | None = None,
) -> list[str]:
    """把一行 shell 指令包成沙箱 argv。

    參數：
        指令: 使用者／模型要執行的完整 shell 指令。
        工作目錄: 指令要在沙箱內的哪個目錄執行；None 表示不切換。
        允許連線: 是否加 --allow-egress；None 表示讀環境設定。
        工作階段識別碼: 用來延續檔案狀態的 session id；None 表示不延續。

    返回值：
        list[str]，可直接交給 `subprocess.run`（不得再加 shell=True）。

    例外：
        沙箱不可用 —— 容器內沒有沙箱可執行檔。

    副作用：可能建立狀態目錄。
    """
    if not 沙箱是否可執行():
        raise 沙箱不可用(f"找不到沙箱可執行檔：{沙箱可執行檔}")

    內層指令 = 指令
    if 工作目錄:
        # 在沙箱內切目錄，而不是靠 subprocess 的 cwd——cwd 設定的是啟動器的目錄，
        # 不保證傳進沙箱的檔案系統視圖。
        安全工作目錄 = shlex.quote(str(工作目錄))
        內層指令 = f"mkdir -p {安全工作目錄} && cd {安全工作目錄} && {指令}"

    argv = [str(沙箱可執行檔), "do", "--write"]

    狀態檔 = 工作階段狀態檔(工作階段識別碼)
    if 狀態檔 is not None:
        # 兩向同步：執行前把上次的檔案解回沙箱，結束時把這次的變更存回去。
        argv.append(f"--sync-tar={狀態檔}")

    if 允許連線 if 允許連線 is not None else 沙箱是否允許連線():
        argv.append("--allow-egress")

    argv += ["--", 沙箱內Shell, "-c", 內層指令]
    return argv

# L1 部署清单（D2-3）

> 执行方：丁一 / Hermes。OpenCode 侧交付物见下；外部账号操作（建应用/建站点/配 ping）在网页后台做。

## 一、PostHog（US 区）site_id（部署前必须）

> D2-3a 起埋点用 PostHog 免费档（永久 100 万事件/月）；GoGoKit 域名停放、Plausible 仅 30 天试用，均弃用。

**key 安全纪律**：`phc_` 密钥**只进 Community Cloud 的环境变量面板**（它本身是 "Write-only、可用于公开应用" 的前端 key，但按纪律不扩散）。代码里不留任何 key 值，只读 `os.environ`。

1. 登录 posthog.com（region=US），建免费 Cloud 项目（Hobby 档 $0），拿到 **Project API key**（`phc_...` 开头）。
   - 门 2（unique users）看面板 Users 计数 + `clean_completed` 事件；门 3 分母用 PostHog 自动上报的 `$pageview`；门 3 分子 = `pro_click` 事件。
   - 第 30 天裁决：手动看面板 **Events/Insights（HogQL）** 即可，无需写脚本拉数（门 2：`clean_completed` 去重用户；门 3：`pro_click` ÷ `$pageview`）。`/api/capture/` 是**写入端点**（前端上报用），别用来读数据。
2. 设置环境变量 `POSTHOG_PROJECT_API_KEY` 再启动：
   ```
   set POSTHOG_PROJECT_API_KEY=phc_你的key   # 只进 Cloud 面板，不写进代码/文档
   streamlit run app.py
   ```
   或写进 `.streamlit/.env`（本地开发，.gitignore 已忽略）/ Community Cloud 的环境变量面板（**推荐，上线走这条**）。
   - 可选：`POSTHOG_API_HOST`（US 区默认 `https://us.posthog.com`，EU 实例改 `https://eu.posthog.com`）。
3. `track.py` 注入方式：`<script async src="{api_host}/static.js">` + `posthog.init(key,{api_host})`。
   未设置 `POSTHOG_PROJECT_API_KEY` 时 **不注入脚本**（页面正常、静默不上报，已自查通过）——上线前必须设置，否则 5 个自定义事件（file_uploaded/clean_started/download_clicked/pro_click/clean_completed）全丢。
4. PostHog 数据保留 6 个月（Hobby 档），30 天观察期足够；1 年后如需长期留存再上 Oracle Cloud 跑 Umami 自托管（L2 再说）。


## 二、Streamlit Community Cloud 部署

1. GitHub 建仓 `quantclean`，推 `app.py` / `cleaner/` / `requirements.txt` / `.streamlit/config.toml`。
   - 不推 `test_files/`、`scripts/`、`__pycache__/`（.gitignore 已忽略）。
2. streamlit.io/cloud → New Application → 连 GitHub 仓库 → 起应用。
   - 启动命令自动从 `requirements.txt` 装依赖（已 pin：streamlit==1.66.0 / pandas==3.0.5 / numpy==2.5.3 / pyarrow==26.0.0）。
   - **Python 版本显式选 3.12 或 3.13**（官方镜像不支持 3.14；本地 C:\Python314 仅是开发/验收环境，部署用 3.12/3.13）。
    - 在 Cloud 的环境变量面板加 `POSTHOG_PROJECT_API_KEY`（必填，US 区 `phc_...` key）+ 可选 `POSTHOG_API_HOST`（US 区默认 `https://us.posthog.com`，可不填）。
3. 公网 URL 形如 `quantclean-<user>.streamlit.app`。

## 三、关键词页验收（规格行 132）— 已完成

- `st.title` 已改为 "MT5 CSV → Backtest Data Cleaner"；`st.set_page_config(page_title=...)` 已改为 "MT5 CSV to Backtest Data"。
- 验收项"页面含 'mt5 csv to backtest' 标题" **已满足**，无需再改。

## 四、UptimeRobot 保活（规格行 111/142）

1. uptimerobot.com 免费注册，加 HTTP(S) Monitor → 填公网 URL，间隔 5 分钟。
2. 防实例休眠白屏流失（30 秒+ 白屏用户跑掉）。
3. **发帖当天 checklist**（规格行 140）：
   - 发帖前 30 分钟手动访问一次公网 URL，确认实例已唤醒、首屏无 30 秒白屏。
   - 发帖后 1 小时盯 PostHog 面板：`$pageview` 开始有量、5 个自定义事件（file_uploaded/clean_started/download_clicked/pro_click/clean_completed）无解析报错。
   - 当天晚间确认 UptimeRobot ping 正常（实例 5 分钟内响应）。

## 五、上线后确认（30 天观察期）

- PostHog 面板（永久免费 100 万事件/月）发一条测试事件确认可见。
- 门 2 / 门 3 判据见规格书第一节（门 2 = PostHog 面板 Users 计数 + `clean_completed` 事件 ≥3 session；门 3 = `pro_click` ÷ `$pageview` ≥2%）。
- 30 天到点按三道门裁决；门 2/3 任一不过 → 停，不进 L2。
- 第 30 天用 PostHog 面板 Events/Insights（HogQL）手动裁决，不用换工具。

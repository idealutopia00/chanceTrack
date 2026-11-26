# 013 v1.0 前端架构与工作台视觉规范

状态：v1.0 架构设计已通过两轮访谈确认（Q2、Q3、Q5、Q8、Q9、Q10、Q11），待高保真视觉原型确认后按里程碑 3 实施。

## 决策：React + TypeScript + Vite + Tailwind CSS SPA

替代 [001-local-stack.md](001-local-stack.md) 中的零构建手写 JS/CSS 与 Jinja SSR 混拼方案：

- **技术栈**：`React 19` + `TypeScript` + `Vite` + `Tailwind CSS v4` + `lucide-react` 图标 + `lightweight-charts`（TradingView 5.x）+ `react-markdown`（配 `remark-gfm`，禁用原始 HTML）。
- **单进程零额外常驻服务**：`frontend/` 编译产物输出至 `chance_track/static/dist/`，由 FastAPI 直接托管静态资源并对前端路由做 SPA fallback，本地依然仅运行一个 `127.0.0.1:8765` Python 服务和一套 LaunchAgent。
- **构建产物不入 Git**：`chance_track/static/dist/` 与 `frontend/node_modules/` 写入 `.gitignore`，由 `frontend/package-lock.json` 锁定依赖，保持 PR diff 干净可读。

## 信息架构：4 个一级视图（无刷新 SPA）

彻底拆除原首页顶部 5 层工具条、底部 4 个手风琴、7 个杂乱 `<dialog>` 及重复的 `/manage` 表单，用左侧窄导航轨（`52px` 图标轨，可悬浮显示文字）切换 4 个职责分明的视图：

1. **工作台（`/` · 默认首页）** — 桌面端全视口三栏布局（`100dvh`，无整页滚动）：
   - **左栏 · 自选列表（`248px`，可一键折叠）**：紧凑搜索框、视图筛选胶囊（全部 / 重点 / 有信号 / 待确认）、按分组排列的标的卡片（名称、代码、最新收盘价、涨跌幅、盘中/异常圆点、信号徽章），支持拖拽排序与点击 `+` 打开命令面板式搜索添加标的。
   - **主画布 · K 线与指标区（自适应最大宽度）**：
     - 精简标的抬头：市场归属、标的名称与代码、等宽大号价格与涨跌幅、交易日/收盘状态胶囊，若处于旧行情断连态仅在抬头下方紧贴一条克制告警条。
     - **单行极简工具条**：`日线 / 周线` 分段开关、`3M / 6M / 1Y / 全部` 范围分段、`指标` 弹出面板、`模板` 下拉、`成交量` 开关、`决策标注` 开关、事件前后跳转。
     - **全高专业图表**：TradingView Lightweight Charts 蜡烛图 + 均线/布林叠加 + 独立成交量轴 + MACD 副图 + 策略事件标记 + **我的历史决策标记**（悬浮/点击直接查看当时判断）。
   - **右栏 · 上下文抽屉（`320px`，可一键折叠释放 100% 图表宽度）**：
     - 3 个干净 Tab，围绕当前选中标的：
       1. `信号与监测`：当前标的最新信号、绑定策略的启停与参数预览、一键「保存为监测 / 同步修改」。
       2. `决策复盘`：顶部紧凑录入卡（决定 / 执行 / 依据，支持勾选附带当前探索依据），下方按时间倒序展示**该标的跨日全部历史决策**（点击即可在图上定位到对应 K 线）。
       3. `相关备忘`：展示关联到当前标的的备忘录，以及快速入口查看全局置顶纪律。
   - **顶部极简全局状态栏（`44px`）**：品牌标识、当前视图名、置顶备忘快捷按钮（点击弹出清爽阅读弹窗）、主题切换、最近检查状态指示灯 + 「立即检查」主按钮。

2. **信号流（`/signals`）**：
   - 顶部关键指标摘要（今日确认事件、盘中待确认、异常标的、下次自动检查倒计时）。
   - 结构化信号事件流（支持按标的、策略、状态筛选；点击任意事件一键跳转工作台并定位到对应 K 线日期）。
   - 最近检查批次与系统通知记录抽屉（替代原首页底部的审计追溯与历史报告手风琴）。

3. **规则库（`/strategies`）**：
   - 按策略类型分组的清晰卡片/表格视图：一眼看清规则名称、核心参数、当前绑定的全部标的徽标。
   - 右侧滑出式规则编辑器（新建、调参、复制副本、等价合并预览、安全删除），焦点与键盘操作严格受控。

4. **备忘录（`/notes`）**：
   - 左栏备忘列表（置顶置前，支持按关联标的筛选与全文搜索）+ 右栏舒适阅读区 / 分栏 Markdown 编辑器。
   - 正文严格保持舒适行宽（`66ch`）、高对比度与柔和段落节奏；多列 Markdown 表格采用清晰表头与等宽代码对齐；一键切换全局置顶与关联标的标签。

## 视觉设计系统（Linear × TradingView 克制金融终端）

- **主题与色彩 Token**：
  - **石墨深色（Dark）**：画布 `#0F1115`、主表面 `#15181E`、浮层 `#1B1F27`、细边框 `rgba(255,255,255,0.08)`、主文字 `#ECEFF4`、次文字 `#8E96A4`、强调色克制青翠 `#3B9A78`。
  - **温润纸白（Light）**：画布 `#F6F5F1`、主表面 `#FFFFFF`、次表面 `#EFECE6`、细边框 `#E2DDD3`、主文字 `#1F1D1A`、次文字 `#6B665E`、强调色深松绿 `#2F6B55`。
  - **涨跌与语义色（中国市场红涨绿跌，低饱和护眼）**：
    - 涨（Up）：Dark `#E06C65` / Light `#C8463E`
    - 跌（Down）：Dark `#3DA67A` / Light `#287E5B`
    - 警告/旧行情：Dark `#D9A45B`（底 `rgba(217,164,91,0.12)`）/ Light `#9A6319`（底 `#FBF3E6`）
- **排版与密度**：
  - 全局价格、涨跌幅、成交量、指标数值、日期强制 `font-variant-numeric: tabular-nums`。
  - 交互反馈克制流畅（`150ms` 缓动，尊重 `prefers-reduced-motion: reduce`）；键盘快捷键：`/` 唤醒标的搜索、`[` 折叠左侧自选、`]` 折叠右侧抽屉、`Esc` 关闭浮层。
- **响应式策略（桌面优先 + 窄屏抽屉）**：
  - `>= 1280px`：完整三栏满屏工作台；
  - `< 1024px`：左右栏自动收起为顶部按钮唤起的抽屉，主图区保持清晰可读与零横向溢出。

## 构建、校验与部署流水线

- `scripts/check_project.py`：
  1. 校验 Python 源码、`catalog.json` 唯一性与交易时段、`uv.lock` 版本；
  2. 运行 `npm --prefix frontend run typecheck` 与 `npm --prefix frontend run build`（校验 `chance_track/static/dist/index.html` 与产物完整）。
- `scripts/install_launchd.py`：
  - 部署到 `~/Library/Application Support/ChanceTrack/runtime/` 前执行前端构建，将 `chance_track/static/dist/` 随 `chance_track/` 一并同步到运行副本；运行期无需 Node。
  - 增加 Schema v5 → v6 的备份与隔离预演门禁，延续旧版恢复保障。
- GitHub Actions CI：增加 Node 步（`npm --prefix frontend ci && npm --prefix frontend run typecheck && npm --prefix frontend run build`），随后运行 `check_project.py` 与 `pytest`。

## 分阶段交付里程碑

1. **里程碑 1（当前）**：更新 `CONTEXT.md`、落地 ADR `012` 与 `013`，并通过高保真可交互 HTML 视觉原型确认风格与布局。
2. **里程碑 2（数据层 + JSON API）**：实现 `catalog.json`、SQLite Schema v6、v5 → v6 无损迁移脚本及完整单元测试、重构 `runner.py` 写入规范表、暴露 `/api/v1/*` JSON API 并确保 `pytest` 全部通过。
3. **里程碑 3（全新 React 前端 + 验收切换）**：初始化 `frontend/`、按确认的视觉原型完成 4 个视图 + TradingView 图表 + 决策标注 + 端到端 Playwright 回归，在真实库副本上完成 v5 → v6 迁移预演，经用户验收后合并 `self-iteration` 并切换本机 `8765` 服务。

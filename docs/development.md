# 开发与维护（v1.0）

## Module 职责

| Module | 职责 |
| --- | --- |
| `catalog` | 读取唯一预设标的目录 `catalog.json` |
| `data` | `fetch`：联网抓取并核验真实行情，不生成信号 |
| `market_sessions` | `apply_sessions`：统一交易日、时区、夜盘与收盘状态 |
| `analysis` | `analyze`：纯行情分析与默认 MACD 计算 |
| `strategies` | `evaluate`、`chart_events`：纯策略规则、叠加图层与事件计算 |
| `schema` | SQLite Schema v6 表定义与 v4/v5 → v6 无损迁移 |
| `storage` | 规范化 SQLite 读写（标的、日线 `market_bars`、检查批次、信号流、决策、备忘、模板、通知） |
| `configuration` | 标的与策略配置校验、乐观锁版本、等价合并与 ETF 迁移 |
| `presentation` | `fetch_state_summary`、`signal_rows`：纯展示语义派生 |
| `notifications` | 生成 macOS 通知文案并按 `signal_events.dedup_key` 去重发送 |
| `runner` | 调度、跨进程检查锁、自动重试、入库与通知串联 |
| `workspace` | 标的中心化图表数据计算、零副作用探索预览与工作台写命令 |
| `web` | FastAPI `/api/v1/*` JSON API 与编译态 React SPA 托管 |
| `frontend/` | React 19 + TypeScript + Vite + Tailwind CSS + Lightweight Charts 前端源码 |

## 前端开发与构建

- 开发联调：分别启动后端 `uv run chance-track serve` 与前端 `npm --prefix frontend run dev`（自动代理 `/api` 至 `127.0.0.1:8765`）。
- 生产构建：`npm --prefix frontend run build` 输出静态产物至 `chance_track/static/dist/`（已列入 `.gitignore`，不入 Git）。

## 提交前验证

```sh
uv run python scripts/check_project.py
uv run pytest -q
```

`scripts/check_project.py` 会依次校验 Python 语法、`catalog.json`、`uv.lock` 一致性、`frontend/` 的 TypeScript 类型检查、Vite 生产构建产物，以及 `tests/check_chart_markers.mjs`（主图事件标记、错层胶囊与决策徽章在桌面与窄屏画布上的零重叠、折叠计数障碍与隐藏事件防误点回归检查）。

## 工作台交互验证

使用隔离的临时 SQLite 合成数据启动测试服务，再运行 `tests/check_v1_app.mjs`（Playwright + 本机 Chrome）验证桌面 `1280×900` 与窄屏 `390×844`、浅/深双主题、4 个一级视图（工作台 / 信号流 / 规则库 / 备忘录）、指标探索、决策追加与 K 线标注、主图 `__ctMarkers` 画布几何零重叠、关联备忘与零横向溢出。

改动图表标记、图内标签或决策徽章布局时，必须在桌面与窄屏（`390×844`）同时验证实际画布矩形（胶囊、箭头、收拢计数圆徽章与决策徽章彼此零重叠且位于画布边界内），并确认隐藏事件后点击画布不触发事件选中。

## Schema v5 → v6 升级与恢复

- `scripts/install_launchd.py` 在停止现有 LaunchAgent 后，会自动将旧库一致性备份与 `runtime-revision.txt` 写入 `~/Library/Application Support/ChanceTrack/backups/schema-v5-to-v6-*`，先在临时副本预演迁移并校验 `PRAGMA quick_check` 与 `PRAGMA foreign_key_check`，预演通过后才构建并切换运行副本。
- 升级后不触发行情抓取或补发历史通知（`signal_events.dedup_key` 与 `notified_at` 完整保留）。

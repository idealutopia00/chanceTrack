# ChanceTrack 维护入口

修改前按任务阅读领域定义与决策，并核对实现和测试。

| 任务 | 先读 | 实现入口 | 定向验证 |
| --- | --- | --- | --- |
| 交易日、夜盘与收盘判定 | [领域定义](CONTEXT.md)、[市场时段](docs/decisions/006-market-sessions.md)、[盘中预览](docs/decisions/005-intraday-preview.md) | [market_sessions](chance_track/market_sessions.py)、[runner](chance_track/runner.py) | `uv run pytest -q tests/test_market_sessions.py tests/test_intraday.py` |
| 策略、过滤与事件 | [组合策略](CONTEXT.md)、[策略管理](docs/decisions/008-strategy-management.md) | [strategies](chance_track/strategies.py)、[analysis](chance_track/analysis.py) | `uv run pytest -q tests/test_strategies.py tests/test_workspace.py` |
| 图表与通知 | [展示语义](CONTEXT.md)、[图表工作台](docs/decisions/009-chart-workspace.md)、[通知去重](docs/decisions/007-notification-dedup.md) | [app.js](chance_track/static/app.js)、[presentation](chance_track/presentation.py)、[notifications](chance_track/notifications.py) | `uv run pytest -q tests/test_workspace.py tests/test_notifications.py`；`node tests/check_updates.mjs` |

提交前运行 [开发指南中的项目检查](docs/development.md#提交前验证)。工作台交互使用隔离的合成数据，参见[工作台交互验证](docs/development.md#工作台交互验证)。

领域词义维护在 [CONTEXT.md](CONTEXT.md)，模块职责与验证方法维护在 [docs/development.md](docs/development.md)，影响行为的设计取舍维护在 [docs/decisions/](docs/decisions/)。

有可追溯证据的返工出现时，在根目录建立或更新 `LESSONS.md`。记录日期、问题、相关提交或 Issue，以及本可发现问题的测试或审查步骤。同一教训在三个不同日期重复出现时，经审查纳入回归测试或开发指南。

## 用 #6 走查

从「策略、过滤与事件」一行可找到现有组合策略在同一根日 K 线上判定的定义。扩展到跨日观察前，在 `docs/decisions/` 确定观察何时开始、何时结束，以及历史回放如何用已完成的日线重现事件。

例如，为「RSI 连续三个交易日低于阈值只产生一次回调提示」先明确第三个连续收盘日触发、持续低于阈值期间不重复提示，以及达到或超过阈值后何时重新计数。用已收盘的合成日线验证前两日无事件、第三日触发、第四日无重复，并验证重放得到同一事件和交易日。

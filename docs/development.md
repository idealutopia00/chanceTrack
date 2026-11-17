# 开发与维护

## Module 职责

| Module | Interface与职责 |
| --- | --- |
| data | fetch：请求并校验真实行情，不生成信号 |
| market_sessions | apply_sessions：统一交易日、时区、夜盘和收盘状态 |
| analysis | analyze：纯MACD与交叉计算，产出图表和信号数据 |
| storage | 不可变报告、追加决策、事件和通知状态持久化 |
| presentation | signal_rows：纯展示语义，区分当前观察与上一收盘日参考 |
| notifications | 使用统一展示结果生成文案并调用macOS通知 |
| runner | 调度、互斥、重试、保存与发送的串联 |
| web | 页面、受保护表单、通知视图；不重新计算指标 |

当前状态和历史参考统一由presentation派生，网页与通知不得各自推断日期/状态。分析规则变更需升级规则版本；仅调整展示不改写报告、不清空事件或通知表。

## 模板和静态资源

主模板负责页面骨架；partials内分别维护chart、asset_details、notification。保持现有表单和图表元素ID，前端脚本依赖这些ID。不要把整页HTML写入app.css；部署前检查会明确拒绝此类错误。

自有Python、JS、CSS与模板已格式化，vendor内的第三方图表源码和许可保持原样。可按需使用以下格式化工具，不增加运行时依赖：

```sh
uvx ruff format chance_track scripts tests
npx --yes prettier --write chance_track/static/app.css chance_track/static/app.js tests/check_updates.mjs
uvx djlint chance_track/templates --reformat --profile=jinja
```

## 提交前验证

```sh
uv run python scripts/check_project.py
uv run pytest -q
node tests/check_updates.mjs
```

`check_project.py`验证Python语法、Jinja模板、静态文件类型、观察清单和锁文件；如果本机有Node，也验证JS语法。安装器在停止LaunchAgent之前调用同一个检查，避免已知坏源文件替换正常版本。

数据库和历史快照不因整理删除。不要通过清库“修复”旧信号；应保留来源、说明语义并添加行为检查。旧预览放在archive，只是当时的演示，不参与运行和取数。

## 截图中黄金11月16日死叉的来源

来自报告#11当时保存的Au99.99真实日线，11月16日DIF−DEA由正转负，因此作为上一已收盘日参考。11月17日未收盘K线当时并无新交叉。之前将两者放进同一表，导致看似重复或像当天新信号；本次整理将两种scope分区，计算和原始快照保持不变。

## 工作台交互验证

`tests/test_workspace.py` 验证探索无正式副作用、模板与配置并发版本、局部绑定的影响范围、独立行情引用、决策探索依据与组合依赖参数。

可选的 `tests/check_workspace.mjs` 使用 Playwright + 本机 Chrome 做完整交互测试，不是运行依赖。先把正式 SQLite 备份到测试文件，再用该文件启动测试服务（默认127.0.0.1:8766）；不要指向正式数据库。通过 `PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/check_workspace.mjs` 执行，`CHANCE_TEST_URL` 和 `CHROME_BIN` 可覆盖测试地址与浏览器路径。测试会保存测试模板、监测配置和决策，并检查桌面/手机布局、指标参数和决策草稿恢复、跨标的模板、历史默认状态及独立依据。

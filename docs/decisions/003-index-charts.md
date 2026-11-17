# ADR 003：直接分析指数 OHLC，并复用开源金融图表

日期：2026-11-17。状态：已实施，取代 ADR 001 中的基金代理分析口径，保留其本地技术栈。

用户明确更正：决策针对指数、需要对应 K 线、希望结合成熟开源项目改善外观。

选择：美股三个真实指数用 Yahoo chart（^GSPC/^NDX/^SOX），中国科创创业50和黄金现货用东方财富日 K。黄金默认 Au99.99，明确它是现货而不是指数。此前用于观察的四只基金不再采集。历史报告维持原值，不将净值伪造为 OHLC。

数据源验证：东方财富 SOX 日 K 此次仅返回 95 个样本，低于约定 120 样本，因此不降低预热门槛；Yahoo ^SOX 返回 500 根，且 meta 标明 INDEX。美股三项统一 Yahoo，避免用名为“纳斯达克”的模糊市场代码替代 ^NDX。两个中国品种各返回 600 根，全部验证了身份和价格关系。

图表选择 TradingView Lightweight Charts 5.0.9，适合蜡烛图、多 Pane、时间轴、十字线及事件标记；保留 Python/Jinja2，不为了页面改版引入 React 或完整交易终端。库和许可文件本地保存，避免本地应用依赖第三方 CDN。Apache-2.0 许可及 NOTICE/TradingView 链接均保留。前端只显示后端已保存指标，不重复计算。

代价：公开接口无 SLA；两年/600 样本窗口与无限历史 EMA 可能略有差异；只包含完整日线，不提供中国当天盘中预警。原始快照及规则版本用于审计。复杂行情或盘中信号需求出现时，应独立设计临时日线与正式日线的状态，不混用。

参考：
- https://github.com/tradingview/lightweight-charts/tree/v5.0.9
- https://tradingview.github.io/lightweight-charts/docs/5.0/panes
- https://tradingview.github.io/lightweight-charts/tutorials/how_to/series-markers
- https://finance.yahoo.com/quote/%5ESOX/
- https://quote.eastmoney.com/zz/2.931643.html
- https://quote.eastmoney.com/q/118.AU9999.html

/* Native dialog replays the same saved notification body without sending anything. */
(() => {
  const button = document.getElementById("show-notification");
  const dialog = document.getElementById("notification-dialog");
  if (!button || !dialog) return;
  button.addEventListener("click", () => {
    if (!dialog.open) dialog.showModal();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (
        event.clientX < bounds.left ||
        event.clientX > bounds.right ||
        event.clientY < bounds.top ||
        event.clientY > bounds.bottom
      )
        dialog.close();
    }
  });
})();

/* Data refresh is separate from chart rendering and works on an empty dashboard. */
(() => {
  const form = document.getElementById("refresh-form");
  if (!form) return;
  const button = document.getElementById("refresh-button");
  const label = document.getElementById("refresh-label");
  const status = document.getElementById("update-status");
  const reportLink = document.getElementById("new-report-link");
  const currentId = Number(document.body.dataset.reportId || 0);
  const dirtyForms = new Set();
  let updating = false,
    polling = false;
  const setStatus = (message, error = false) => {
    status.textContent = message;
    status.classList.toggle("error", error);
  };
  const offerReport = (id, message) => {
    setStatus(message);
    reportLink.href = `/reports/${id}`;
    reportLink.hidden = false;
  };
  const relativeTimes = () =>
    document.querySelectorAll("[data-relative-time]").forEach((node) => {
      const value = new Date(node.dateTime),
        seconds = Math.max(
          0,
          Math.floor((Date.now() - value.getTime()) / 1000),
        );
      if (!Number.isFinite(seconds)) return;
      const age =
        seconds < 60
          ? "刚刚"
          : seconds < 3600
            ? `${Math.floor(seconds / 60)} 分钟前`
            : seconds < 86400
              ? `${Math.floor(seconds / 3600)} 小时前`
              : `${Math.floor(seconds / 86400)} 天前`;
      const clock = value.toLocaleTimeString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      });
      node.textContent = `${clock} · ${age}`;
      node.title = node.dateTime;
    });
  document.querySelectorAll(".decision-panel form").forEach((decisionForm) => {
    decisionForm.addEventListener("input", () => dirtyForms.add(decisionForm));
    decisionForm.addEventListener("change", () => dirtyForms.add(decisionForm));
    decisionForm.addEventListener("submit", () =>
      dirtyForms.delete(decisionForm),
    );
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirtyForms.size) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  async function pollStatus() {
    relativeTimes();
    if (updating || polling || document.hidden) return;
    polling = true;
    try {
      const response = await fetch("/api/update-status", {
        cache: "no-store",
      });
      if (!response.ok) throw Error("无法获取更新状态");
      const state = await response.json();
      button.disabled = state.running;
      if (state.latest && state.latest.id > currentId)
        offerReport(
          state.latest.id,
          "已有新报告。当前页面和未保存的判断已保留。",
        );
      else if (state.running)
        setStatus("后台正在采集行情，完成后会提示新报告。");
      else if (status.textContent === "后台正在采集行情，完成后会提示新报告。")
        setStatus("后台检查已结束，暂无新报告。");
    } catch (error) {
      setStatus("暂时无法连接本地服务，请稍后重试。", true);
    } finally {
      polling = false;
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (updating || button.disabled) return;
    updating = true;
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
    label.textContent = "更新中";
    setStatus("正在按各市场交易时段采集最新行情。");
    reportLink.hidden = true;
    try {
      const response = await fetch("/refresh", {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.detail || "更新失败，请稍后重试");
      if (dirtyForms.size)
        offerReport(
          result.report_id,
          `更新完成${result.failures ? `，仍有 ${result.failures} 项采集失败` : ""}。未保存的判断已保留，请先保存再查看新报告。`,
        );
      else window.location.assign(`/reports/${result.report_id}`);
    } catch (error) {
      setStatus(error.message || "更新失败，请稍后重试", true);
    } finally {
      updating = false;
      button.disabled = false;
      form.setAttribute("aria-busy", "false");
      label.textContent = "立即更新";
    }
  });
  relativeTimes();
  // Poll metadata only; this never fetches market prices or overwrites a user's draft.
  setInterval(pollStatus, 30000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollStatus();
  });
})();

/* Chart rendering only: indicator values come from the saved Python result. */
(() => {
  const dataNode = document.getElementById("chart-data");
  if (!dataNode) return;
  const items = JSON.parse(dataNode.textContent);
  const container = document.getElementById("chart");
  const empty = document.getElementById("chart-empty");
  const markerToggle = document.getElementById("show-markers");
  const eventPanel = document.getElementById("chart-events");
  const eventList = document.getElementById("chart-event-list");
  const eventDetail = document.getElementById("chart-event-detail");
  const library = window.LightweightCharts;
  let chart,
    candles,
    dif,
    dea,
    histogram,
    markers,
    selected,
    range = 126;
  let overlaySeries = [];
  const number = (value) =>
    Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  const dayKey = (time) =>
    typeof time === "string"
      ? time
      : time && typeof time === "object"
        ? `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`
        : "";
  function message(title, detail) {
    empty.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = title;
    const text = document.createElement("span");
    text.textContent = detail;
    empty.append(heading, text);
    empty.hidden = false;
  }
  function legend(point) {
    for (const [id, key] of [
      ["legend-dif", "dif"],
      ["legend-dea", "dea"],
      ["legend-hist", "histogram"],
    ]) {
      document.getElementById(id).textContent = point
        ? point[key].toFixed(3)
        : "—";
    }
  }
  function showOHLC(row) {
    const target = document.getElementById("ohlc");
    target.replaceChildren();
    if (!row) return;
    const date = document.createElement("span");
    date.className = "date";
    date.textContent = row.time;
    target.append(date);
    for (const [name, key] of [
      ["开", "open"],
      ["高", "high"],
      ["低", "low"],
      [
        selected?.result?.is_provisional &&
        row.time === selected.result.bar_date
          ? "现"
          : "收",
        "close",
      ],
    ]) {
      const span = document.createElement("span");
      span.textContent = name;
      const value = document.createElement("b");
      value.textContent = number(row[key]);
      span.append(value);
      target.append(span);
    }
  }
  function setRange() {
    if (!chart || !selected?.result?.chart) return;
    const count = selected.result.chart.candles.length;
    if (range === "all") chart.timeScale().fitContent();
    else
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, count - Number(range)),
        to: count + 3,
      });
  }
  function chartEvents() {
    return (
      selected?.result?.chart?.strategy_events ??
      selected?.result?.chart?.crosses ??
      []
    );
  }
  function showEvent(date) {
    const rows = chartEvents().filter((event) => event.time === date);
    const bars = selected.result.chart.candles;
    const index = bars.findIndex((bar) => bar.time === date);
    if (!rows.length || index < 0) return;
    eventPanel.open = true;
    eventDetail.replaceChildren();
    for (const event of rows) {
      const row = document.createElement("p");
      const config = event.config;
      const params = [
        ...Object.entries(config?.params ?? {}).map(
          ([key, value]) => `${key} ${value}`,
        ),
        ...Object.values(config?.dependencies ?? {}).map(
          (rule) =>
            `${rule.name} ${Object.entries(rule.params ?? {})
              .map(([key, value]) => `${key} ${value}`)
              .join(" / ")}`,
        ),
        config?.filter,
      ]
        .filter(Boolean)
        .join(" / ");
      row.textContent = [
        date,
        event.name ?? "MACD",
        event.version,
        params,
        event.event ?? (event.kind === "golden" ? "金叉" : "死叉"),
        event.provisional ? "待确认" : "收盘确认",
        `价格 ${event.price ?? bars[index].close}`,
        event.detail,
      ]
        .filter(Boolean)
        .join(" · ");
      eventDetail.append(row);
    }
    eventList
      .querySelectorAll("button")
      .forEach((button) =>
        button.setAttribute(
          "aria-current",
          String(button.dataset.eventDate === date),
        ),
      );
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, index - 25),
      to: index + 8,
    });
    chart.setCrosshairPosition(bars[index].close, date, candles);
    showOHLC(bars[index]);
  }
  function renderEventList() {
    eventList.replaceChildren();
    eventDetail.replaceChildren();
    const dates = new Map();
    for (const { time } of chartEvents())
      dates.set(time, (dates.get(time) ?? 0) + 1);
    eventPanel.hidden = dates.size === 0;
    for (const date of [...dates.keys()].sort().reverse()) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.eventDate = date;
      button.textContent = `${date} · ${dates.get(date)} 条`;
      button.addEventListener("click", () => showEvent(date));
      eventList.append(button);
    }
  }
  function setMarkers() {
    if (!markers || !selected?.result?.chart) return;
    const meanings = {
      金叉: ["金叉", "up"],
      死叉: ["死叉", "down"],
      向上突破上轨: ["上破上轨", "up"],
      向下突破下轨: ["下破下轨", "down"],
      上轨外回归轨内: ["跌回上轨内", "down"],
      下轨外回归轨内: ["升回下轨内", "up"],
    };
    markers.setMarkers(
      markerToggle.checked
        ? chartEvents()
            .slice()
            .sort((a, b) => a.time.localeCompare(b.time))
            .map((cross) => {
              const event =
                cross.event || (cross.kind === "golden" ? "金叉" : "死叉");
              const [label, direction] = meanings[event] || [event, null];
              const up = direction === "up";
              return {
                time: cross.time,
                id: `event:${cross.time}`,
                position: up ? "belowBar" : "aboveBar",
                color: up ? "#dfc080" : "#a896dd",
                shape: direction ? (up ? "arrowUp" : "arrowDown") : "circle",
                size: cross.provisional ? 0 : 1,
                text: `${cross.provisional ? "○ 待确认 " : ""}${label}`,
              };
            })
        : [],
    );
  }
  function initialize() {
    if (!library) return false;
    chart = library.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "#11191f" },
        textColor: "#76909f",
        fontSize: 10,
        attributionLogo: true,
        panes: {
          separatorColor: "#29363f",
          separatorHoverColor: "#59736b",
          enableResize: false,
        },
      },
      grid: {
        vertLines: { color: "#19252d" },
        horzLines: { color: "#1d2b33" },
      },
      crosshair: {
        mode: library.CrosshairMode.Normal,
        vertLine: { color: "#668a7d", labelBackgroundColor: "#334f43" },
        horzLine: { color: "#668a7d", labelBackgroundColor: "#334f43" },
      },
      rightPriceScale: {
        borderColor: "#283740",
        minimumWidth: 68,
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "#283740",
        timeVisible: false,
        rightOffset: 4,
      },
      localization: { locale: "zh-CN" },
    });
    candles = chart.addSeries(library.CandlestickSeries, {
      upColor: "#ed7e84",
      downColor: "#64c9af",
      borderVisible: false,
      wickUpColor: "#ed7e84",
      wickDownColor: "#64c9af",
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    histogram = chart.addSeries(
      library.HistogramSeries,
      {
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: { type: "price", precision: 3, minMove: 0.001 },
      },
      1,
    );
    dif = chart.addSeries(
      library.LineSeries,
      {
        color: "#dfc080",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 3,
      },
      1,
    );
    dea = chart.addSeries(
      library.LineSeries,
      {
        color: "#a896dd",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 3,
      },
      1,
    );
    histogram.createPriceLine({
      price: 0,
      color: "#49606d",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: false,
      title: "",
    });
    chart.timeScale().subscribeVisibleLogicalRangeChange((view) => {
      if (view)
        window.dispatchEvent(
          new CustomEvent("chart:range", {
            detail: { asset_id: selected?.asset.id, view },
          }),
        );
    });
    chart.panes()[0].setHeight(380);
    chart.panes()[1].setHeight(180);
    markers = library.createSeriesMarkers(candles, []);
    chart.subscribeClick((event) => {
      if (event.hoveredObjectId?.startsWith?.("event:"))
        showEvent(event.hoveredObjectId.slice(6));
    });
    chart.subscribeCrosshairMove((param) => {
      const content = selected?.result?.chart;
      if (!content) return;
      const key = dayKey(param.time);
      const point =
        (selected.activeMacd || content.macd).find((row) => row.time === key) ||
        (selected.activeMacd || content.macd).at(-1);
      const row =
        content.candles.find((row) => row.time === key) ||
        content.candles.at(-1);
      legend(point);
      showOHLC(row);
    });
    return true;
  }
  function select(id, options = {}) {
    const previousId = selected?.asset.id;
    const previousRange = chart?.timeScale().getVisibleLogicalRange();
    selected = items.find((item) => item.asset.id === id);
    if (!selected) return;
    if (!options.silent)
      window.dispatchEvent(
        new CustomEvent("chart:selected", { detail: { asset_id: id } }),
      );
    try {
      sessionStorage.setItem("selectedAsset", id);
    } catch {}
    const tag = document.getElementById("chart-session");
    if (tag) {
      const phase = selected.result?.session?.phase;
      const pendingLabels = {
        day: "交易中未收盘",
        night: "夜盘未收盘",
        break: "休市待续",
        settling: "收盘待确认",
      };
      tag.textContent = selected.result?.is_provisional
        ? `日线 · ${pendingLabels[phase] || "盘中未收盘"}`
        : selected.result?.bar_date
          ? "日线 · 已收盘"
          : "—";
      tag.classList.toggle(
        "provisional",
        Boolean(selected.result?.is_provisional),
      );
    }
    const macdTag = document.getElementById("macd-session");
    if (macdTag)
      macdTag.textContent = selected.result?.is_provisional ? "临时值" : "";
    document.querySelectorAll("[data-asset]").forEach((button) => {
      const active = button.dataset.asset === id;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document
      .querySelectorAll("[data-detail]")
      .forEach((panel) => (panel.hidden = panel.dataset.detail !== id));
    document.getElementById("chart-title").textContent = selected.asset.label;
    const symbol = document.createElement("small");
    symbol.textContent = selected.asset.ticker || selected.asset.code || "";
    document.getElementById("chart-title").append(symbol);
    document.getElementById("chart-market").textContent = selected.asset.market
      ? `${selected.asset.market} · ${selected.asset.currency}`
      : "历史报告 · 基金净值";
    const content = selected.result?.chart;
    const quoteTime =
      selected.fetched_at ||
      selected.result?.bar_date ||
      selected.result?.nav_date;
    document.getElementById("chart-quote-time").textContent = quoteTime
      ? `行情 ${quoteTime.replace("T", " ")}`
      : "";
    if (!content) {
      if (chart) {
        chart.remove();
        chart = null;
        overlaySeries = [];
      }
      document.getElementById("chart-layers").replaceChildren();
      legend(null);
      showOHLC(null);
      message(
        "暂无可用 K 线",
        selected.result
          ? "这份历史报告采用基金净值，保留原始结果，不生成虚构 OHLC。"
          : "该标的行情未成功采集，请查看右侧说明。",
      );
      return;
    }
    if (!library) {
      message("图表组件未加载", "请刷新页面，或检查本地图表静态资源。");
      return;
    }
    try {
      if (!chart) initialize();
      candles.setData(content.candles);
      for (const entry of overlaySeries) chart.removeSeries(entry.series);
      overlaySeries = [];
      const layerControls = document.getElementById("chart-layers");
      layerControls.replaceChildren();
      const colors = ["#dfc080", "#a896dd", "#64c9af", "#ed7e84", "#79b7e8"];
      for (const [i, layer] of (content.overlays || []).entries()) {
        const series = chart.addSeries(library.LineSeries, {
          color: colors[i % colors.length],
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        series.setData(layer.points);
        overlaySeries.push({ id: layer.id, series });
        const label = document.createElement("label"),
          input = document.createElement("input");
        input.type = "checkbox";
        input.checked = true;
        input.dataset.layerId = layer.id;
        input.addEventListener("change", () => {
          series.applyOptions({ visible: input.checked });
          window.dispatchEvent(
            new CustomEvent("chart:layer", {
              detail: { id: layer.id, visible: input.checked },
            }),
          );
        });
        label.append(input, document.createTextNode(layer.name));
        layerControls.append(label);
      }
      const macdConfigs = content.macd_configs || [
        { name: "MACD 历史配置", points: content.macd },
      ];
      let activeMacd = macdConfigs[0]?.points || content.macd;
      function drawMacd(points) {
        activeMacd = points;
        histogram.setData(
          points.map((p) => ({
            time: p.time,
            value: p.histogram,
            color: p.histogram >= 0 ? "#ed7e8499" : "#64c9af99",
          })),
        );
        dif.setData(points.map((p) => ({ time: p.time, value: p.dif })));
        dea.setData(points.map((p) => ({ time: p.time, value: p.dea })));
        legend(points.at(-1));
        selected.activeMacd = points;
      }
      if (macdConfigs.length > 1) {
        const select = document.createElement("select");
        select.setAttribute("aria-label", "MACD 图表配置");
        macdConfigs.forEach((config, i) => {
          const option = document.createElement("option");
          option.value = String(i);
          option.textContent = config.name;
          select.append(option);
        });
        select.addEventListener("change", () =>
          drawMacd(macdConfigs[Number(select.value)].points),
        );
        layerControls.append(select);
      }
      const macdLabel = document.createElement("label"),
        macdInput = document.createElement("input");
      macdInput.type = "checkbox";
      macdInput.checked = true;
      macdInput.dataset.layerId = "macd";
      macdInput.addEventListener("change", () => {
        [dif, dea, histogram].forEach((series) =>
          series.applyOptions({ visible: macdInput.checked }),
        );
        window.dispatchEvent(
          new CustomEvent("chart:layer", {
            detail: { id: "macd", visible: macdInput.checked },
          }),
        );
      });
      macdLabel.append(macdInput, document.createTextNode("MACD 副图"));
      layerControls.append(macdLabel);
      histogram.setData(
        content.macd.map((point) => ({
          time: point.time,
          value: point.histogram,
          color: point.histogram >= 0 ? "#ed7e8499" : "#64c9af99",
        })),
      );
      dif.setData(
        content.macd.map((point) => ({
          time: point.time,
          value: point.dif,
        })),
      );
      dea.setData(
        content.macd.map((point) => ({
          time: point.time,
          value: point.dea,
        })),
      );
      drawMacd(activeMacd);
      const hasMacd = macdConfigs.length > 0;
      macdLabel.hidden = !hasMacd;
      document.querySelector(".macd-legend").hidden = !hasMacd;
      for (const series of [dif, dea, histogram]) {
        series.applyOptions({ visible: hasMacd });
        series.moveToPane(hasMacd ? 1 : 0);
      }
      macdInput.checked = hasMacd;
      if (hasMacd && chart.panes().length > 1) {
        chart.panes()[0].setHeight(380);
        chart.panes()[1].setHeight(180);
      }
      setMarkers();
      renderEventList();
      if (options.view) chart.timeScale().setVisibleLogicalRange(options.view);
      else if (previousId === id && previousRange && options.keepRange)
        chart.timeScale().setVisibleLogicalRange(previousRange);
      else setRange();
      legend(activeMacd.at(-1));
      showOHLC(content.candles.at(-1));
      empty.hidden = true;
      container.dataset.ready = "true";
    } catch (error) {
      container.dataset.ready = "false";
      console.error(error);
      message("图表加载失败", "请刷新页面；完整行情快照仍可在下方查看。");
    }
  }
  document
    .querySelectorAll("[data-asset]")
    .forEach((button) =>
      button.addEventListener("click", () => select(button.dataset.asset)),
    );
  document.querySelectorAll("[data-range]").forEach((button) =>
    button.addEventListener("click", () => {
      range = button.dataset.range;
      document
        .querySelectorAll("[data-range]")
        .forEach((other) => other.classList.toggle("active", other === button));
      setRange();
    }),
  );
  document.querySelectorAll("[data-focus-date]").forEach((button) =>
    button.addEventListener("click", () => {
      select(button.dataset.focusAsset);
      if (!chart || !selected?.result?.chart) return;
      const index = selected.result.chart.candles.findIndex(
        (row) => row.time === button.dataset.focusDate,
      );
      if (index >= 0) {
        chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, index - 25),
          to: index + 8,
        });
        container.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }),
  );
  markerToggle.addEventListener("change", setMarkers);
  window.ChanceChart = {
    select,
    current: () => selected,
    view: () => chart?.timeScale().getVisibleLogicalRange(),
    restoreView: (view) => {
      if (chart && view) chart.timeScale().setVisibleLogicalRange(view);
    },
    show(item, options = {}) {
      const index = items.findIndex((i) => i.asset.id === item.asset.id);
      if (index < 0) items.push(item);
      else items[index] = item;
      select(item.asset.id, { silent: true, keepRange: true, ...options });
    },
    layers: (visibility) => {
      document.querySelectorAll("[data-layer-id]").forEach((input) => {
        if (!(input.dataset.layerId in visibility) || input.closest("[hidden]"))
          return;
        const visible = visibility[input.dataset.layerId] !== false;
        if (input.checked !== visible) {
          input.checked = visible;
          input.dispatchEvent(new Event("change"));
        }
      });
    },
  };
  let initialId = items[0]?.asset.id;
  try {
    const saved = sessionStorage.getItem("selectedAsset");
    if (items.some((item) => item.asset.id === saved)) initialId = saved;
  } catch {}
  select(initialId);
  if (!initialId)
    message("尚无行情快照", "添加标的并点击立即检查，或从观察列表加载行情。");
})();

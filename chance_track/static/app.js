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
  const allEventsButton = document.getElementById("chart-all-events");
  let eventSelection,
    showAllEvents = false;
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
  let period = "day",
    displayedBars = [],
    displayedDay = new Map();
  const previousEventButton = document.getElementById("chart-previous-event");
  const nextEventButton = document.getElementById("chart-next-event");
  const latestButton = document.getElementById("chart-latest");
  const crosshairReadings = document.getElementById("chart-crosshair-readings");
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
    const count = displayedBars.length;
    const windowSize =
      period === "week" ? Math.ceil(Number(range) / 5) : Number(range);
    if (range === "all") chart.timeScale().fitContent();
    else
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, count - windowSize),
        to: count + 3,
      });
  }
  function displayCandles(daily) {
    displayedDay = new Map();
    if (period === "day") {
      for (const bar of daily) displayedDay.set(bar.time, bar.time);
      return daily;
    }
    const weeks = new Map();
    for (const bar of daily) {
      const monday = new Date(`${bar.time}T00:00:00Z`);
      monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
      const key = monday.toISOString().slice(0, 10);
      if (!weeks.has(key)) weeks.set(key, { candle: { ...bar }, dates: [] });
      const { candle, dates } = weeks.get(key);
      dates.push(bar.time);
      Object.assign(candle, {
        time: bar.time,
        close: bar.close,
        high: Math.max(candle.high, bar.high),
        low: Math.min(candle.low, bar.low),
      });
    }
    for (const { candle, dates } of weeks.values())
      for (const date of dates) displayedDay.set(date, candle.time);
    return [...weeks.values()].map(({ candle }) => candle);
  }
  function displayedPoints(points) {
    return period === "day"
      ? points
      : points.filter((point) => displayedDay.get(point.time) === point.time);
  }
  function syncCrosshair(row) {
    showOHLC(row);
    crosshairReadings.replaceChildren();
    if (!row) return;
    const content = selected.result.chart;
    const date = row.time;
    const macd = (selected.activeMacd ?? content.macd).find(
      (point) => point.time === date,
    );
    const readings = macd
      ? [
          `DIF ${number(macd.dif)} · DEA ${number(macd.dea)} · 柱 ${number(macd.histogram)}`,
        ]
      : [];
    for (const layer of content.overlays ?? []) {
      const point = layer.points.find((entry) => entry.time === date);
      if (point?.value != null)
        readings.push(`${layer.name} ${number(point.value)}`);
    }
    const configList = selected.result.strategies ?? [];
    for (const strategy of configList) {
      const config = strategy.config;
      if (!config) continue;
      const params = [config, ...Object.values(config.dependencies ?? {})]
        .map((rule) =>
          Object.entries(rule.params ?? {})
            .map(([key, value]) => `${key} ${value}`)
            .join(" / "),
        )
        .filter(Boolean);
      readings.push(
        [strategy.name, strategy.version, ...params]
          .filter(Boolean)
          .join(" · "),
      );
    }
    if (!configList.length && macd && !content.macd_configs)
      readings.push("MACD 历史配置 · fast 12 / slow 26 / signal 9");
    for (const event of chartEvents().filter(
      (entry) => displayedDay.get(entry.time) === date,
    )) {
      readings.push(
        `${event.time} · ${eventTitle(event, eventReadings(event).macd)} · ${eventState(event)}`,
      );
    }
    legend(macd);
    if (!readings.length) return;
    const heading = document.createElement("strong");
    heading.textContent = `${date} · 日线指标与策略`;
    const list = document.createElement("ul");
    for (const reading of readings) {
      const item = document.createElement("li");
      item.textContent = reading;
      list.append(item);
    }
    crosshairReadings.append(heading, list);
  }
  function adjacentEvent(direction) {
    const dates = [...eventsByDate().keys()].sort();
    const reference = eventSelection?.date ?? displayedBars.at(-1)?.time;
    if (direction < 0)
      return dates.findLast((date) =>
        eventSelection ? date < reference : date <= reference,
      );
    return dates.find((date) => date > reference);
  }
  function syncEventNavigation() {
    previousEventButton.disabled = !adjacentEvent(-1);
    nextEventButton.disabled = !adjacentEvent(1);
    latestButton.disabled = !displayedBars.length;
  }
  previousEventButton.addEventListener("click", () =>
    showEvent(adjacentEvent(-1)),
  );
  nextEventButton.addEventListener("click", () => showEvent(adjacentEvent(1)));
  latestButton.addEventListener("click", () => {
    const previous = displayedBars.find(
      (bar) => bar.time === displayedDay.get(eventSelection?.date),
    );
    if (previous) candles.update({ ...previous }, true);
    eventSelection = undefined;
    eventDetail.replaceChildren();
    for (const button of eventList.querySelectorAll("button"))
      button.removeAttribute("aria-current");
    setMarkers();
    setRange();
    chart.clearCrosshairPosition();
    syncCrosshair(displayedBars.at(-1));
    syncEventNavigation();
  });
  document
    .getElementById("chart-period")
    .addEventListener("change", (event) => {
      period = event.currentTarget.value;
      if (!selected) return;
      const layers = Object.fromEntries(
        [...document.querySelectorAll("[data-layer-id]")].map((input) => [
          input.dataset.layerId,
          input.checked,
        ]),
      );
      select(selected.asset.id, { silent: true });
      window.ChanceChart.layers(layers);
    });
  function chartEvents() {
    return (
      selected?.result?.chart?.strategy_events ??
      selected?.result?.chart?.crosses ??
      []
    );
  }
  function eventName(event) {
    return event.event ?? (event.kind === "golden" ? "金叉" : "死叉");
  }
  function eventState(event) {
    return event.provisional === true
      ? "待确认"
      : event.provisional === false
        ? "收盘确认"
        : "确认状态未记录";
  }
  function eventReadings(event) {
    const { chart: content } = selected.result;
    const date = event.time;
    const matches = (selected.result.strategies ?? []).filter(
      (strategy) => strategy.name === event.name,
    );
    const saved = matches.length === 1 ? matches[0] : null;
    const rule = event.config ?? saved?.config;
    const macdPoints = event.kind
      ? content.macd
      : content.macd_configs?.find(
          (series) => series.id === (rule?.trigger ?? rule?.id),
        )?.points;
    const macd = macdPoints?.find((point) => point.time === date);
    const overlayIds = [
      rule?.id,
      rule?.trigger,
      rule?.filter && rule?.band,
    ].filter(Boolean);
    const overlays = (content.overlays ?? [])
      .filter((layer) => overlayIds.some((id) => layer.id.startsWith(`${id}:`)))
      .flatMap((layer) => {
        const point = layer.points.find((reading) => reading.time === date);
        return point?.value != null
          ? [
              {
                label: layer.id.split(":").at(-1),
                name: layer.name,
                value: point.value,
              },
            ]
          : [];
      });
    const config =
      rule ??
      (event.kind && macd
        ? { params: { fast: 12, slow: 26, signal: 9 } }
        : null);
    const price =
      event.price ?? content.candles.find((bar) => bar.time === date)?.close;
    return { macd, overlays, config, price, saved };
  }
  function eventTitle(event, macd) {
    const name = eventName(event);
    return macd && (name === "金叉" || name === "死叉") ? `MACD ${name}` : name;
  }
  function eventSummary(event, { macd, overlays, price }) {
    const name = eventName(event);
    const readings = [];
    if (macd && (name === "金叉" || name === "死叉"))
      readings.push(
        `DIF ${number(macd.dif)} ${name === "金叉" ? "上穿" : "下穿"} DEA ${number(macd.dea)}`,
      );
    else
      readings.push(
        ...overlays.map(({ label, value }) => `${label} ${number(value)}`),
      );
    if (price != null)
      readings.push(
        `${event.provisional === true ? "盘中价" : event.provisional === false ? "收盘价" : "价格"} ${number(price)}`,
      );
    return readings.join("｜");
  }
  function showEvent(date, locate = true, offset = undefined) {
    const rows = eventsByDate().get(date);
    if (!rows?.length) return;
    const bars = displayedBars;
    const time = displayedDay.get(date);
    const index = bars.findIndex((bar) => bar.time === time);
    if (index < 0) return;
    offset ??= eventSelection?.date === date ? eventSelection.offset : 0;
    const event = rows[offset] ?? rows[0];
    const activeOffset = rows.indexOf(event);
    if (eventSelection?.date !== date) {
      const previous = bars.find(
        (bar) => bar.time === displayedDay.get(eventSelection?.date),
      );
      if (previous) candles.update({ ...previous }, true);
      candles.update(
        { ...bars[index], borderColor: "#dfc080", wickColor: "#dfc080" },
        true,
      );
    }
    eventSelection = { date, offset: activeOffset };
    let active = [...eventList.querySelectorAll("button")].find(
      (button) =>
        button.dataset.eventDate === date &&
        Number(button.dataset.eventOffset) === activeOffset,
    );
    if (!active) {
      showAllEvents = true;
      renderEventList();
      active = [...eventList.querySelectorAll("button")].find(
        (button) =>
          button.dataset.eventDate === date &&
          Number(button.dataset.eventOffset) === activeOffset,
      );
    }
    eventPanel.open = true;
    if (active?.closest("details") !== eventPanel)
      active?.closest("details")?.setAttribute("open", "");
    for (const button of eventList.querySelectorAll("button"))
      button.setAttribute("aria-current", String(button === active));
    const { macd, overlays, config, price, saved } = eventReadings(event);
    const full = [
      date,
      event.name ?? "MACD",
      event.version ?? saved?.version,
      eventTitle(event, macd),
      eventState(event),
      price != null && `价格 ${price}`,
      macd && `DIF ${macd.dif} · DEA ${macd.dea} · 柱 ${macd.histogram}`,
      ...overlays.map(({ name, value }) => `${name} ${value}`),
      event.detail ??
        (saved?.event_date === date && saved.event === event.event
          ? saved.detail
          : null),
    ]
      .filter(Boolean)
      .join(" · ");
    const details = document.createElement("details");
    const label = document.createElement("summary");
    label.textContent = "触发详情与参数";
    const description = document.createElement("p");
    description.textContent = full;
    details.append(label, description);
    if (config) {
      const parameters = document.createElement("pre");
      parameters.textContent = JSON.stringify(config, null, 2);
      details.append(parameters);
    }
    eventDetail.replaceChildren(details);
    setMarkers();
    syncEventNavigation();
    if (!locate) return;
    if (active) {
      const rowBounds = active.getBoundingClientRect();
      const listBounds = eventList.getBoundingClientRect();
      if (rowBounds.top < listBounds.top)
        eventList.scrollTop += rowBounds.top - listBounds.top;
      if (rowBounds.bottom > listBounds.bottom)
        eventList.scrollTop += rowBounds.bottom - listBounds.bottom;
    }
    chart.timeScale().setVisibleLogicalRange({
      from: Math.max(0, index - 25),
      to: index + 8,
    });
    chart.setCrosshairPosition(bars[index].close, time, candles);
    syncCrosshair(bars[index]);
  }
  function resetEventPanel() {
    eventList.replaceChildren();
    eventDetail.replaceChildren();
    eventPanel.hidden = true;
    eventPanel.open = false;
    eventSelection = undefined;
    showAllEvents = false;
  }
  function eventsByDate() {
    const dates = new Map();
    for (const event of chartEvents()) {
      if (!dates.has(event.time)) dates.set(event.time, []);
      dates.get(event.time).push(event);
    }
    return dates;
  }
  function renderEventList() {
    const entries = [...eventsByDate()].sort(([a], [b]) => b.localeCompare(a));
    eventPanel.hidden = entries.length === 0;
    allEventsButton.hidden = entries.length <= 5 || showAllEvents;
    eventList.replaceChildren();
    for (const [date, events] of showAllEvents
      ? entries
      : entries.slice(0, 5)) {
      const group = document.createElement("div");
      for (const [offset, event] of events.entries()) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.eventDate = date;
        button.dataset.eventOffset = offset;
        const readings = eventReadings(event);
        const title = document.createElement("strong");
        title.textContent = `${eventTitle(event, readings.macd)} · ${date} · ${eventState(event)}`;
        const summary = document.createElement("small");
        summary.textContent = eventSummary(event, readings);
        button.append(title, summary);
        button.addEventListener("click", () => showEvent(date, true, offset));
        if (offset === 0) {
          group.append(button);
          if (events.length === 1) continue;
          const more = document.createElement("details");
          const label = document.createElement("summary");
          label.textContent = `另 ${events.length - 1} 条`;
          more.append(label);
          group.append(more);
        } else {
          group.lastElementChild.append(button);
        }
      }
      eventList.append(group);
    }
  }
  allEventsButton.addEventListener("click", () => {
    showAllEvents = true;
    renderEventList();
    if (eventSelection)
      showEvent(eventSelection.date, false, eventSelection.offset);
  });
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
        ? [...eventsByDate()]
            .sort(([left], [right]) => left.localeCompare(right))
            .filter(([date]) => displayedDay.has(date))
            .map(([date, events]) => {
              const event = eventName(events[0]);
              const [label, firstDirection] = meanings[event] || [event, null];
              const grouped = events.length > 1;
              const direction = events.every(
                (entry) => meanings[eventName(entry)]?.[1] === firstDirection,
              )
                ? firstDirection
                : null;
              const up = direction === "up";
              const provisional = events.every(
                (entry) => entry.provisional === true,
              );
              const pendingCount = events.filter(
                (entry) => entry.provisional === true,
              ).length;
              const active = eventSelection?.date === date;
              const glyph = !direction ? "○" : up ? "⇧" : "⇩";
              return {
                time: displayedDay.get(date),
                id: `event:${date}`,
                position: up ? "belowBar" : "aboveBar",
                color: active ? "#ffffff" : up ? "#dfc080" : "#a896dd",
                shape: !direction ? "circle" : up ? "arrowUp" : "arrowDown",
                size: provisional ? 0 : 1,
                text: `${provisional ? `${glyph} 待确认 ` : ""}${grouped ? `${events.length} 条事件` : label}${pendingCount && !provisional ? `（${pendingCount} 条待确认）` : ""}`,
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
            detail: { asset_id: selected?.asset.id, view, period },
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
      const row =
        displayedBars.find((bar) => bar.time === key) || displayedBars.at(-1);
      syncCrosshair(row);
    });
    return true;
  }
  function select(id, options = {}) {
    const previousId = selected?.asset.id;
    const previousEvent = previousId === id ? eventSelection : null;
    const previousAll = previousId === id && showAllEvents;
    resetEventPanel();
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
    displayedBars = displayCandles(content?.candles ?? []);
    document.getElementById("chart-period-label").textContent =
      `K 线 ${period === "week" ? "周线" : "日线"} · 信号 日线`;
    syncEventNavigation();
    const quoteTime =
      selected.fetched_at ||
      selected.result?.bar_date ||
      selected.result?.nav_date;
    const lastQuote = selected.last_quote;
    document.getElementById("chart-quote-time").textContent =
      selected.status === "error"
        ? lastQuote
          ? `本次更新失败 · 行情交易日 ${lastQuote.bar_date}${lastQuote.fetched_at ? ` · 上次成功采集 ${lastQuote.fetched_at.replace("T", " ")}` : ""}`
          : "本次更新失败 · 无可用行情"
        : quoteTime
          ? `行情 ${quoteTime.replace("T", " ")}`
          : "";
    if (!content) {
      resetEventPanel();
      if (chart) {
        chart.remove();
        chart = null;
        overlaySeries = [];
      }
      document.getElementById("chart-layers").replaceChildren();
      legend(null);
      syncCrosshair(null);
      message(
        "暂无可用 K 线",
        selected.result
          ? "这份历史报告采用基金净值，保留原始结果，不生成虚构 OHLC。"
          : "该标的行情未成功采集，请查看右侧说明。",
      );
      return;
    }
    if (!library) {
      resetEventPanel();
      message("图表组件未加载", "请刷新页面，或检查本地图表静态资源。");
      return;
    }
    try {
      if (!chart) initialize();
      candles.setData(displayedBars.map((bar) => ({ ...bar })));
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
        series.setData(
          displayedPoints(layer.points).map((point) => ({ ...point })),
        );
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
        const visiblePoints = displayedPoints(points);
        histogram.setData(
          visiblePoints.map((p) => ({
            time: p.time,
            value: p.histogram,
            color: p.histogram >= 0 ? "#ed7e8499" : "#64c9af99",
          })),
        );
        dif.setData(visiblePoints.map((p) => ({ time: p.time, value: p.dif })));
        dea.setData(visiblePoints.map((p) => ({ time: p.time, value: p.dea })));
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
      showAllEvents = previousAll;
      renderEventList();
      eventPanel.open = !eventPanel.hidden;
      setMarkers();
      if (previousEvent)
        showEvent(previousEvent.date, false, previousEvent.offset);
      if (options.view) chart.timeScale().setVisibleLogicalRange(options.view);
      else if (previousId === id && previousRange && options.keepRange)
        chart.timeScale().setVisibleLogicalRange(previousRange);
      else setRange();
      syncCrosshair(displayedBars.at(-1));
      syncEventNavigation();
      empty.hidden = true;
      container.dataset.ready = "true";
    } catch (error) {
      resetEventPanel();
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
      const index = displayedBars.findIndex(
        (row) => row.time === displayedDay.get(button.dataset.focusDate),
      );
      showEvent(button.dataset.focusDate);
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
    focusEvent: (date) => {
      if (!chart || !selected?.result?.chart) return;
      showEvent(date);
      container.scrollIntoView({ block: "center" });
    },
    current: () => selected,
    period: () => period,
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

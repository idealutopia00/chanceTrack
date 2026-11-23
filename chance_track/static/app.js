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
    zeroLine,
    priceMarks,
    macdMarks,
    selected,
    range = 126;
  let overlaySeries = [], bands = new Map(), activeMacdId;
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
      const { macd, config } = eventReadings(event);
      readings.push(
        `${event.time} · ${eventTitle(event, macd, config)} · ${eventState(event)}`,
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
  function savedEventRule(event) {
    const matches = (selected.result.strategies ?? []).filter(
      (strategy) => strategy.name === event.name,
    );
    return matches.length === 1 ? matches[0] : null;
  }
  function eventReadings(event) {
    const { chart: content } = selected.result;
    const date = event.time;
    const saved = savedEventRule(event);
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
  function eventTitle(event, macd, config) {
    const name = eventName(event);
    const rule = config?.dependencies?.[config.trigger] ?? config;
    const params = rule?.params;
    if (macd && (name === "金叉" || name === "死叉"))
      return params ? `MACD（${params.fast}/${params.slow}/${params.signal}）${name}` : `MACD ${name}`;
    if (params && bandEvents[name])
      return `布林带（${params.period}，${params.stddev}）${name}`;
    return name;
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
        { ...bars[index], borderColor: color("gold"), wickColor: color("gold") },
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
      eventTitle(event, macd, config),
      eventSummary(event, { macd, overlays, price }),
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
    details.open = true;
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
        title.textContent = `${eventTitle(event, readings.macd, readings.config)} · ${date} · ${eventState(event)}`;
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
  const bandEvents = {
    向上突破上轨: ["上轨", true],
    向下突破下轨: ["下轨", false],
    上轨外回归轨内: ["上轨", false],
    下轨外回归轨内: ["下轨", true],
  };
  function setMarkers() {
    if (!priceMarks || !selected?.result?.chart) return;
    const price = [], macd = [];
    const dateIndex = new Map(displayedBars.map((bar, index) => [bar.time, index]));
    if (markerToggle.checked) {
      const macdIds = new Set(selected.result.chart.macd_configs?.map(({ id }) => id));
      for (const [date, events] of eventsByDate()) {
        const time = displayedDay.get(date);
        if (!time) continue;
        for (const [offset, event] of events.entries()) {
          const config = event.config ?? savedEventRule(event)?.config;
          const bar = displayedBars[dateIndex.get(time)];
          const id = config?.trigger ?? config?.id;
          const name = eventName(event);
          const item = {
            time, date, offset, shape: "circle",
            up: name === "金叉", provisional: event.provisional,
          };
          if (event.kind || macdIds.has(id)) {
            const point = displayedMacd.get(time);
            if (!point || (!event.kind && id !== activeMacdId)) continue;
            item.value = (point.dif + point.dea) / 2;
            item.shape = "diamond";
            macd.push(item);
          } else {
            const band = bands.get(id);
            if (bandEvents[name]) {
              const [rail, up] = bandEvents[name];
              const value = band?.rails.get(rail)?.get(time);
              if (!band?.visible() || value == null) continue;
              const side = rail === "上轨" ? -1 : 1;
              const bodyLow = Math.min(bar.open, bar.close);
              const bodyHigh = Math.max(bar.open, bar.close);
              const anchor = value >= bodyLow && value <= bodyHigh
                ? (side < 0 ? bodyHigh : bodyLow)
                : value;
              Object.assign(item, { value: anchor, up, shape: "triangle", side });
            } else {
              item.value = Math.max(bar.open, bar.close);
              item.side = -1;
            }
            price.push(item);
          }
        }
      }
    }
    priceMarks.set(price);
    macdMarks.set(macd);
  }
  let displayedMacd = new Map();
  function markerPrimitive(series, visible = () => true) {
    let points = [], hits = [], requestUpdate;
    const renderer = {
      draw(target) {
        hits = [];
        if (!visible()) return;
        target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
          for (const point of points) {
            const x = chart.timeScale().timeToCoordinate(point.time);
            const y = series.priceToCoordinate(point.value);
            if (x == null || y == null || x < -8 || x > mediaSize.width + 8) continue;
            const anchor = y + (point.side ?? 0) * 6;
            let cx = x, cy = anchor;
            // Fan out coincident markers while keeping them close to their series.
            while (hits.some((hit) => Math.hypot(hit.x - cx, hit.y - cy) < 9))
              cy += (point.side ?? 1) * 9;
            hits.push({ x: cx, y: cy, point });
            ctx.beginPath();
            const selected = eventSelection?.date === point.date && eventSelection.offset === point.offset;
            ctx.fillStyle = ctx.strokeStyle = color(selected ? "text" : point.up ? "gold" : "violet");
            if (point.shape === "diamond") {
              ctx.moveTo(cx, cy - 4);
              ctx.lineTo(cx + 4, cy);
              ctx.lineTo(cx, cy + 4);
              ctx.lineTo(cx - 4, cy);
            } else if (point.shape === "triangle") {
              const tip = point.up ? -4 : 4;
              ctx.moveTo(cx, cy + tip);
              ctx.lineTo(cx + 4, cy - tip);
              ctx.lineTo(cx - 4, cy - tip);
            } else {
              ctx.arc(cx, cy, 3, 0, 2 * Math.PI);
            }
            ctx.closePath();
            if (point.provisional) ctx.stroke();
            else ctx.fill();
          }
        });
      },
    };
    const view = { renderer: () => renderer, zOrder: () => "top" };
    const primitive = {
      attached: (params) => { requestUpdate = params.requestUpdate; },
      paneViews: () => [view],
      hitTest(x, y) {
        if (!visible()) return null;
        const hit = hits.find((hit) => Math.hypot(hit.x - x, hit.y - y) <= 5);
        if (!hit) return null;
        return { externalId: `event:${hit.point.date}:${hit.point.offset}`, zOrder: "top", cursorStyle: "pointer" };
      },
      set(next) {
        hits = [];
        points = next;
        requestUpdate();
      },
    };
    series.attachPrimitive(primitive);
    return primitive;
  }
  function drawBands(target) {
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      const scale = chart.timeScale();
      for (const band of bands.values()) {
        if (!band.visible()) continue;
        const upper = [], lower = [];
        for (const [time, value] of band.rails.get("上轨") ?? []) {
          const x = scale.timeToCoordinate(time);
          const top = candles.priceToCoordinate(value);
          const lowerValue = band.rails.get("下轨")?.get(time);
          if (lowerValue == null) continue;
          const bottom = candles.priceToCoordinate(lowerValue);
          if (x == null || top == null || bottom == null) continue;
          upper.push([x, top]);
          lower.push([x, bottom]);
        }
        if (!upper.length) continue;
        ctx.save();
        ctx.globalAlpha = 0.10;
        ctx.fillStyle = color(band.color);
        ctx.beginPath();
        ctx.moveTo(...upper[0]);
        for (const point of upper.slice(1)) ctx.lineTo(...point);
        for (const point of lower.reverse()) ctx.lineTo(...point);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
    });
  }
  const theme = getComputedStyle(document.documentElement);
  const color = (name) => theme.getPropertyValue(`--${name}`).trim();
  const candleColors = () => ({
    upColor: color("up"),
    downColor: color("down"),
    wickUpColor: color("up"),
    wickDownColor: color("down"),
  });
  function chartColors() {
    return {
      layout: {
        background: { type: "solid", color: color("panel") },
        textColor: color("muted"),
        panes: { separatorColor: color("line"), separatorHoverColor: color("mint") },
      },
      grid: {
        vertLines: { color: color("surface") },
        horzLines: { color: color("surface") },
      },
      crosshair: {
        vertLine: { color: color("mist"), labelBackgroundColor: color("button-primary") },
        horzLine: { color: color("mist"), labelBackgroundColor: color("button-primary") },
      },
      rightPriceScale: { borderColor: color("line") },
      timeScale: { borderColor: color("line") },
    };
  }
  function initialize() {
    if (!library) return false;
    chart = library.createChart(container, {
      autoSize: true,
      layout: {
        fontSize: 10,
        attributionLogo: true,
        panes: { enableResize: false },
      },
      crosshair: { mode: library.CrosshairMode.Normal },
      rightPriceScale: {
        minimumWidth: 68,
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: { timeVisible: false, rightOffset: 4 },
      localization: { locale: "zh-CN" },
    });
    chart.applyOptions(chartColors());
    candles = chart.addSeries(library.CandlestickSeries, {
      ...candleColors(),
      borderVisible: false,
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
        color: color("gold"),
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
        color: color("violet"),
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 3,
      },
      1,
    );
    zeroLine = histogram.createPriceLine({
      price: 0,
      color: color("line"),
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
    priceMarks = markerPrimitive(candles);
    macdMarks = markerPrimitive(dif, () => dif.options().visible);
    candles.attachPrimitive({
      paneViews: () => [{ renderer: () => ({ draw: drawBands }), zOrder: () => "bottom" }],
    });
    chart.subscribeClick((event) => {
      if (!event.point) return;
      const marks = event.paneIndex === 1 ? macdMarks : priceMarks;
      const hit = marks.hitTest(event.point.x, event.point.y);
      if (!hit) return;
      const [, date, offset] = hit.externalId.split(":");
      showEvent(date, true, Number(offset));
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
  let sparklineAsset;
  function showSparkline(item) {
    const svg = document.getElementById("chart-sparkline");
    const result = item.result;
    const rows = result?.chart?.candles ?? [];
    const prices = (result?.is_provisional ? rows.slice(0, -1) : rows)
      .slice(-20)
      .map((bar) => bar.close);
    if (svg.toggleAttribute("hidden", prices.length < 2 || !prices.every(Number.isFinite))) {
      if (sparklineAsset !== item.asset.id) sparklineAsset = null;
      return;
    }
    const low = Math.min(...prices), span = Math.max(...prices) - low;
    const points = prices.map((price, index) => [
      4 + (112 * index) / (prices.length - 1),
      span ? 28 - (24 * (price - low)) / span : 16,
    ]);
    svg.querySelector("polyline").setAttribute("points", points.join(" "));
    const [x, y] = points.at(-1);
    const dot = svg.querySelector("circle");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    svg.setAttribute("aria-label", `最近 ${prices.length} 个已收盘交易日走势`);
    if (sparklineAsset !== item.asset.id) {
      svg.classList.remove("draw");
      svg.getBoundingClientRect();
      svg.classList.add("draw");
      sparklineAsset = item.asset.id;
    }
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
      if (document.body.dataset.historical !== "true")
        localStorage.setItem("selectedAsset", id);
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
      const provisional =
        selected.result?.is_provisional ?? selected.last_quote?.is_provisional === 1;
      tag.textContent = provisional
        ? pendingLabels[phase] || "盘中未收盘"
        : selected.result?.bar_date || selected.last_quote?.is_provisional === 0
          ? "已收盘"
          : "";
      tag.classList.toggle(
        "provisional",
        Boolean(provisional),
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
    showSparkline(selected);
    const content = selected.result?.chart;
    displayedBars = displayCandles(content?.candles ?? []);
    document.getElementById("chart-period-label").textContent =
      `K 线 ${period === "week" ? "周线" : "日线"} · 信号 日线`;
    syncEventNavigation();
    const lastQuote = selected.last_quote;
    const tradeDate = selected.result?.bar_date ?? selected.result?.nav_date ?? lastQuote?.bar_date;
    const price = content?.candles?.at(-1)?.close ?? selected.result?.close ?? lastQuote?.close;
    const fetchedAt = selected.fetched_at ?? lastQuote?.fetched_at;
    document.getElementById("chart-price").textContent =
      price == null ? "—" : price.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
    document.getElementById("chart-quote-date").textContent =
      tradeDate ? `交易日 ${tradeDate}` : "";
    document.getElementById("chart-quote-time").textContent =
      fetchedAt ? `采集 ${fetchedAt.replace("T", " ")}` : "";
    const warning = document.getElementById("chart-quote-warning");
    const warnings = selected.result?.warnings ?? [];
    if (selected.status === "error")
      warning.textContent = selected.mode === "exploration"
        ? `正式检查失败 · 探索行情截至 ${tradeDate || "未知日期"}`
        : lastQuote
          ? `本次未能更新，当前显示 ${tradeDate} ${lastQuote.is_provisional === 0 ? "收盘数据" : "行情"}`
          : "本次未能更新，暂无可用行情";
    else if (warnings.length)
      warning.textContent = `行情截至 ${tradeDate || "未知日期"} · ${warnings.join("；")}`;
    else if (selected.carried_forward)
      warning.textContent = `沿用上次采集 · 行情截至 ${tradeDate || "未知日期"}`;
    else if (document.body.dataset.historical === "true" && tradeDate)
      warning.textContent = `历史快照 · 行情截至 ${tradeDate}`;
    else warning.textContent = "";
    warning.hidden = !warning.textContent;
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
      bands = new Map();
      let focusedBand;
      const colors = ["gold", "violet", "down", "up", "mist"];
      const configFor = (id) =>
        selected.result.strategies?.find((strategy) => strategy.id === id)?.config ??
        selected.result.strategies?.map((strategy) => strategy.config?.dependencies?.[id]).find(Boolean);
      function refreshLayers() {
        for (const { series, visible } of overlaySeries)
          series.applyOptions({ visible: visible() });
        for (const [id, band] of bands)
          band.focus.setAttribute("aria-pressed", String(id === focusedBand));
        setMarkers();
      }
      function layerToggle(id, title) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = true;
        input.dataset.layerId = id;
        input.addEventListener("change", () => {
          refreshLayers();
          window.dispatchEvent(
            new CustomEvent("chart:layer", { detail: { id, visible: input.checked } }),
          );
        });
        label.append(input, document.createTextNode(title));
        layerControls.append(label);
        return input;
      }
      for (const [i, layer] of (content.overlays || []).entries()) {
        const separator = layer.id.lastIndexOf(":");
        const id = layer.id.slice(0, separator);
        const rail = layer.id.slice(separator + 1);
        let band;
        if (["上轨", "中轨", "下轨"].includes(rail) &&
            (layer.kind?.startsWith("boll") || configFor(id)?.kind?.startsWith("boll"))) {
          band = bands.get(id);
          if (!band) {
            const params = configFor(id)?.params;
            const title = params ? `布林带（${params.period}，${params.stddev}）` : layer.name.split(" · ")[0];
            const input = layerToggle(layer.id, title);
            const focus = document.createElement("button");
            focus.type = "button";
            focus.textContent = "突出";
            focus.setAttribute("aria-label", `突出${title}`);
            focus.setAttribute("aria-pressed", "false");
            focus.addEventListener("click", () => {
              focusedBand = focusedBand === id ? undefined : id;
              refreshLayers();
            });
            input.parentElement.after(focus);
            band = { color: colors[i % colors.length], focus, rails: new Map(),
              visible: () => focusedBand ? focusedBand === id : input.checked };
            bands.set(id, band);
          }
        }
        const points = displayedPoints(layer.points);
        const seriesColor = band?.color ?? colors[i % colors.length];
        const series = chart.addSeries(library.LineSeries, {
          color: color(seriesColor),
          lineWidth: 1,
          lineStyle: band && rail === "中轨" ? library.LineStyle.Dashed : library.LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        series.setData(points.map((point) => ({ ...point })));
        const input = band ? null : layerToggle(layer.id, layer.name);
        overlaySeries.push({ series, color: seriesColor, visible: band?.visible ?? (() => input.checked) });
        band?.rails.set(rail, new Map(points.map(({ time, value }) => [time, value])));
      }
      const macdConfigs = content.macd_configs || [
        { name: "MACD 历史配置", points: content.macd },
      ];
      function drawMacd(config) {
        const points = config?.points ?? [];
        activeMacdId = config?.id;
        const visiblePoints = displayedPoints(points);
        displayedMacd = new Map(visiblePoints.map((point) => [point.time, point]));
        const up = color("up"), down = color("down");
        histogram.setData(
          visiblePoints.map((p) => ({
            time: p.time,
            value: p.histogram,
            color: p.histogram >= 0 ? up : down,
          })),
        );
        dif.setData(visiblePoints.map((p) => ({ time: p.time, value: p.dif })));
        dea.setData(visiblePoints.map((p) => ({ time: p.time, value: p.dea })));
        legend(points.at(-1));
        selected.activeMacd = points;
        setMarkers();
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
          drawMacd(macdConfigs[Number(select.value)]),
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
        setMarkers();
        window.dispatchEvent(
          new CustomEvent("chart:layer", {
            detail: { id: "macd", visible: macdInput.checked },
          }),
        );
      });
      macdLabel.append(macdInput, document.createTextNode("MACD 副图"));
      layerControls.append(macdLabel);
      drawMacd(macdConfigs[0]);
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
  window.addEventListener("chancetrack:theme", () => {
    if (!chart || !selected) return;
    chart.applyOptions(chartColors());
    candles.applyOptions(candleColors());
    dif.applyOptions({ color: color("gold") });
    dea.applyOptions({ color: color("violet") });
    zeroLine.applyOptions({ color: color("line") });
    overlaySeries.forEach(({ series, color: name }) => {
      series.applyOptions({ color: color(name) });
    });
    const up = color("up"), down = color("down");
    histogram.setData(histogram.data().map((point) => ({
      ...point,
      color: point.value >= 0 ? up : down,
    })));
    const highlighted = displayedBars.find(
      (bar) => bar.time === displayedDay.get(eventSelection?.date),
    );
    if (highlighted) {
      const gold = color("gold");
      candles.update({ ...highlighted, borderColor: gold, wickColor: gold }, true);
    }
    setMarkers();
  });
  let initialId = items[0]?.asset.id;
  try {
    const saved =
      localStorage.getItem("selectedAsset") ??
      sessionStorage.getItem("selectedAsset");
    if (items.some((item) => item.asset.id === saved)) initialId = saved;
  } catch {}
  select(initialId);
  if (!initialId)
    message("尚无行情快照", "添加标的并点击立即检查，或从观察列表加载行情。");
})();

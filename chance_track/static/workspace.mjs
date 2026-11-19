/* Workspace commands and drafts. Chart rendering stays in app.js; calculations stay in Python. */
import {
  renderWatchlist as renderAssets,
  reorderAssets,
} from "./watchlist.mjs";
(() => {
  const root = document.getElementById("workspace-bootstrap");
  if (!root) return;
  const boot = JSON.parse(root.textContent);
  const originals = new Map(
    JSON.parse(document.getElementById("chart-data").textContent).map(
      (item) => [item.asset.id, item],
    ),
  );
  const chart = window.ChanceChart;
  const $ = (id) => document.getElementById(id);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const read = (key, fallback) => {
    try {
      return JSON.parse(localStorage.getItem(key)) ?? fallback;
    } catch {
      return fallback;
    }
  };
  const write = (key, value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      status("浏览器存储不可用，当前操作可继续，但草稿不能自动恢复", true);
    }
  };
  const viewKey = "chanceTrack.workspace.v1";
  const state = read(viewKey, { views: {}, layers: {}, layout: {} });
  state.views ||= {};
  const assetViewKey = (id, period = chart.period()) =>
    period === "week" ? `${id}:week` : id;
  state.layers ||= {};
  state.layout ||= {};
  let config,
    templateList = [],
    selected,
    source,
    draft = [],
    activeTemplate = null,
    currentPreview = null,
    exploring = false;
  let previewTimer,
    previewSequence = 0,
    configurationBusy = false,
    editingAsset;
  const cache = new Map();
  const status = (text, error = false) => {
    $("workspace-status").textContent = text;
    $("workspace-status").classList.toggle("error", error);
  };
  const draftKey = (id) =>
    `chanceTrack.exploration.${boot.report_id || "live"}.${id}`;
  const persist = () => {
    state.selected = selected;
    write(viewKey, state);
  };
  function saveDraft() {
    if (selected && exploring)
      write(draftKey(selected), {
        strategies: draft,
        source,
        template: activeTemplate,
      });
    persist();
  }
  function button(text, action, cls = "") {
    const node = document.createElement("button");
    node.type = "button";
    node.textContent = text;
    node.className = cls;
    node.addEventListener("click", action);
    return node;
  }
  function text(tag, value, cls = "") {
    const node = document.createElement(tag);
    node.textContent = value;
    node.className = cls;
    return node;
  }
  async function request(url, payload) {
    const response = await fetch(
      url,
      payload
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, csrf: boot.csrf }),
          }
        : { cache: "no-store" },
    );
    let result;
    try {
      result = await response.json();
    } catch {
      throw Error("本地服务暂不可用，请重试；草稿已保留");
    }
    if (!response.ok)
      throw Error(
        typeof result.detail === "string"
          ? result.detail
          : "请求参数无效，请检查输入",
      );
    return result;
  }
  const dialogStatus = (dialog, message, error = false) => {
    const node = dialog.querySelector(".dialog-status");
    if (node) {
      node.textContent = message;
      node.classList.toggle("error", error);
    }
  };
  function openDialog(id) {
    const dialog = $(id);
    dialogStatus(dialog, "");
    if (!dialog.open) dialog.showModal();
  }
  document
    .querySelectorAll("[data-open]")
    .forEach((node) =>
      node.addEventListener("click", () => openDialog(node.dataset.open)),
    );
  document.querySelectorAll(".workspace-dialog").forEach((dialog) => {
    dialog
      .querySelector("[data-close]")
      .addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        const b = dialog.getBoundingClientRect();
        if (
          event.clientX < b.left ||
          event.clientX > b.right ||
          event.clientY < b.top ||
          event.clientY > b.bottom
        )
          dialog.close();
      }
    });
  });
  function setMode(exploration = false) {
    const base = boot.historical ? "历史报告" : "正式检查结果";
    $("workspace-mode").textContent = exploration
      ? `${boot.historical ? "历史行情探索" : "探索预览"} · 不属于正式检查信号 · 行情 ${currentPreview?.result.bar_date || "—"}`
      : `${base} · ${boot.report_id ? `报告 #${boot.report_id}` : "暂无报告"}`;
    $("workspace-mode").classList.toggle("exploring", exploration);
  }
  function expandDependencies(strategies) {
    const result = clone(strategies),
      seen = new Set(result.map((s) => s.id));
    const reserved = new Set([
      ...seen,
      ...config.strategies.map((rule) => rule.id),
      ...result.flatMap((rule) => [rule.trigger, rule.band]),
    ]);
    const available = new Map(config.strategies.map((rule) => [rule.id, rule]));
    for (const rule of draft) available.set(rule.id, rule);
    const aliases = new Map();
    for (const s of result)
      if (s.kind === "combination")
        for (const field of ["trigger", ...(s.filter ? ["band"] : [])]) {
          const id = s[field];
          const pinned = s.dependencies?.[id] ?? available.get(id);
          const visible = result.find((rule) => rule.id === id);
          if (pinned && visible && !sameBaseRule(pinned, visible)) {
            const copies = aliases.get(id) ?? [];
            let alias = copies.find((rule) => sameBaseRule(pinned, rule))?.id;
            if (!alias) {
              alias = `explore_dep_${result.length}`;
              while (reserved.has(alias)) alias += "_";
              const copy = { ...pinned, id: alias };
              result.push(copy);
              copies.push(copy);
              aliases.set(id, copies);
              reserved.add(alias);
              seen.add(alias);
            }
            s.dependencies ??= {};
            s.dependencies[alias] = { ...pinned, id: alias };
            if (s.trigger !== s.band) delete s.dependencies[id];
            s[field] = alias;
            continue;
          }
          if (!seen.has(id)) {
            const dependency = pinned;
            if (dependency) {
              result.push(clone(dependency));
              seen.add(id);
            }
          }
        }
    return result;
  }
  function sameBaseRule(left, right) {
    const keys = Object.keys(left.params);
    return (
      left.kind === right.kind &&
      keys.length === Object.keys(right.params).length &&
      keys.every((key) => left.params[key] === right.params[key])
    );
  }
  function reportStrategies(id) {
    const saved = originals.get(id)?.result?.strategies;
    if (saved) return saved.map((s) => clone(s.config));
    const reportItem = originals.get(id);
    if (reportItem?.result?.chart)
      return [
        {
          id: "macd",
          name: "MACD 默认",
          kind: "macd",
          group: "趋势",
          params: { fast: 12, slow: 26, signal: 9 },
        },
      ];
    return [];
  }
  function restoreLayers() {
    chart?.layers(state.layers);
  }
  function updateBasis() {
    document.querySelectorAll(".decision-panel form").forEach((form) => {
      const id = form.querySelector('[name="asset_id"]').value;
      const choice = form.querySelector("[data-basis-choice]");
      const hidden = form.querySelector("[data-exploration-basis]");
      const usable =
        currentPreview &&
        id === selected &&
        source?.report_id === boot.report_id;
      choice.options[1].disabled = !usable;

      hidden.value =
        choice.value === "exploration" && usable
          ? JSON.stringify({
              source,
              strategies: expandDependencies(draft),
            })
          : "";
      form.querySelector("[data-basis-help]").textContent = hidden.value
        ? "将单独保存当前参数、行情引用和探索计算结果。"
        : "依据原始报告；探索图表不会覆盖正式信号。";
    });
  }
  function renderResults(result) {
    const node = $("exploration-results");
    node.replaceChildren();
    for (const s of result.strategies || []) {
      const card = text("div", "", "exploration-result");
      card.append(text("strong", s.name));
      if (s.is_provisional)
        card.append(
          text(
            "p",
            `${s.bar_date} · ${s.preview_event || "无盘中事件"} · 未收盘待确认`,
          ),
          ...((s.stateful ||
            s.requires_completed_pair ||
            s.kind === "donchian") &&
          s.preview_detail
            ? [text("small", s.preview_detail)]
            : []),
        );
      card.append(
        text(
          "p",
          `${s.event_date} · ${s.event || (s.state === "ready" ? "无事件" : "无法判断")} · 收盘参考`,
        ),
        text("small", s.detail),
      );
      node.append(card);
    }
    if (!(result.strategies || []).length)
      node.append(text("p", "未添加指标，当前仅观察K线。"));
  }
  async function previewNow() {
    if (!selected || !source) return;
    const sequence = ++previewSequence,
      id = selected,
      strategies = expandDependencies(draft),
      snapshotSource = clone(source);
    saveDraft();
    status("正在计算快照预览…");
    try {
      const result = await request("/api/workspace/preview", {
        source: snapshotSource,
        asset_id: id,
        strategies,
      });
      if (sequence !== previewSequence || selected !== id) return;
      currentPreview = result;
      chart.show(result, { view: state.views[assetViewKey(id)] });
      restoreLayers();
      $("exploration-panel").hidden = false;
      $("exploration-time").textContent =
        `行情交易日 ${result.result.bar_date} · 原检查 ${result.checked_at.replace("T", " ").slice(0, 19)} · 采集 ${result.fetched_at?.replace("T", " ").slice(0, 19) || "历史未记录"}`;
      renderResults(result.result);
      setMode(true);
      updateBasis();
      status("探索草稿已保留 · 未改变自动监测");
    } catch (error) {
      if (sequence === previewSequence) {
        currentPreview = null;
        updateBasis();
        status(error.message, true);
      }
    }
  }
  function schedulePreview() {
    exploring = true;
    renderIndicators();
    clearTimeout(previewTimer);
    ++previewSequence;
    currentPreview = null;
    updateBasis();
    saveDraft();
    previewTimer = setTimeout(previewNow, 280);
  }
  function renderIndicators() {
    const strip = $("indicator-strip");
    strip.replaceChildren();
    strip.append(text("span", "图表指标", "muted"));
    for (const s of draft) {
      const pill = text("div", "", "indicator-pill");
      pill.append(button(`${s.name} ⚙`, () => editIndicator(s.id)));
      const origin = s.origin_id || s.id,
        bound = config.assets
          .find((a) => a.id === selected)
          ?.strategies.includes(origin);
      const rule = config.strategies.find((x) => x.id === origin);
      const same =
        rule &&
        ["kind", "params", "trigger", "filter", "band"].every(
          (key) => JSON.stringify(rule[key]) === JSON.stringify(s[key]),
        );
      pill.append(
        button(bound && same ? "监测中" : bound ? "保存监测" : "加入监测", () =>
          monitor(s.id),
        ),
        button("×", () => {
          draft = draft.filter((x) => x.id !== s.id);
          renderIndicators();
          schedulePreview();
        }),
      );
      strip.append(pill);
    }
    strip.append(button("＋ 指标", () => openDialog("indicator-dialog")));
  }
  async function selectAsset(id, options = {}) {
    if (!config) return;
    clearTimeout(previewTimer);
    if (selected !== id) {
      if (selected) saveDraft();
      selected = id;
      renderCatalog();
    }
    ++previewSequence;
    currentPreview = null;
    const original = originals.get(id),
      saved = read(draftKey(id), null);
    source = original ? { report_id: boot.report_id } : null;
    draft = reportStrategies(id);
    activeTemplate = null;
    if (
      saved &&
      (saved.source?.report_id === boot.report_id ||
        (!original && saved.source?.quote_id))
    ) {
      source = saved.source;
      draft = saved.strategies;
      activeTemplate = saved.template;
    } else if (!boot.historical && state.template) {
      const template = allTemplates().find(
        (t) => t.template_id === state.template,
      );
      if (template) {
        draft = clone(template.strategies);
        activeTemplate = template.template_id;
      }
    }
    exploring = Boolean(saved || activeTemplate || !original);
    if (original) {
      chart.show(clone(original), { view: state.views[assetViewKey(id)] });
      setMode(false);
      $("exploration-panel").hidden = true;
    } else {
      chart.show({
        asset: config.assets.find((a) => a.id === id) || {
          id,
          label: "加载标的",
        },
        result: null,
      });
      if (!source) {
        status("正在加载该标的行情…");
        try {
          const value =
            cache.get(id) ||
            (await request("/api/workspace/quote", { asset_id: id }));
          cache.set(id, value);
          if (selected !== id) return;
          source = value.source;
        } catch (error) {
          if (selected !== id) return;
          status(error.message, true);
          renderIndicators();
          renderWatchlist();
          return;
        }
      }
    }
    persist();
    renderIndicators();
    renderWatchlist();
    updateBasis();
    if ((!options.reportOnly && (saved || activeTemplate)) || !original)
      await previewNow();
    else restoreLayers();
  }
  window.addEventListener("chart:selected", (event) => {
    if (config) selectAsset(event.detail.asset_id);
  });
  window.addEventListener("chart:range", (event) => {
    if (event.detail.asset_id) {
      state.views[assetViewKey(event.detail.asset_id, event.detail.period)] =
        event.detail.view;
      persist();
    }
  });
  window.addEventListener("chart:layer", (event) => {
    state.layers[event.detail.id] = event.detail.visible;
    persist();
  });
  function field(parent, label, value, onInput, options) {
    const wrap = text("label", label, "field-label");
    const input = document.createElement(options ? "select" : "input");
    if (options)
      for (const [v, t] of options) {
        const opt = text("option", t);
        opt.value = v;
        input.append(opt);
      }
    input.value = value ?? "";
    input.addEventListener(options ? "change" : "input", () =>
      onInput(input.value),
    );
    wrap.append(input);
    parent.append(wrap);
    return input;
  }
  function editIndicator(id) {
    const s = draft.find((x) => x.id === id);
    if (!s) return;
    const node = $("indicator-fields");
    node.replaceChildren();
    field(node, "名称", s.name, (value) => {
      s.name = value;
      renderIndicators();
      schedulePreview();
    });
    if (s.kind === "combination") {
      const choices = new Map(config.strategies.map((rule) => [rule.id, rule]));
      for (const rule of draft)
        if (rule.kind !== "combination") choices.set(rule.id, rule);
      for (const rule of Object.values(s.dependencies ?? {}))
        choices.set(rule.id, rule);
      const rules = [...choices.values()];
      field(
        node,
        "触发规则",
        s.trigger,
        (value) => {
          s.trigger = value;
          schedulePreview();
        },
        rules
          .filter((x) => x.kind !== "combination")
          .map((x) => [x.id, describeRule(x)]),
      );
      field(
        node,
        "过滤条件",
        s.filter,
        (value) => {
          s.filter = value;
          schedulePreview();
        },
        [
          ["", "无过滤"],
          ["above_mid", "收盘高于布林中轨"],
          ["below_mid", "收盘低于布林中轨"],
        ],
      );
      field(
        node,
        "布林配置",
        s.band,
        (value) => {
          s.band = value;
          schedulePreview();
        },
        rules
          .filter((x) => x.kind.startsWith("boll"))
          .map((x) => [x.id, describeRule(x)]),
      );
    } else
      for (const key of Object.keys(s.params)) {
        const labels = {
          fast: "短周期",
          slow: "长周期",
          signal: "信号周期",
          period: "布林周期",
          stddev: "标准差倍数",
          trend: "趋势均线",
          recovery: "修复均线",
          rsi: "RSI 周期",
          threshold: "RSI 阈值",
          entry: "突破周期",
          exit: "退出周期",
          multiple: "通道倍数",
          relative_period: "比值均线周期",
        };
        if (key === "benchmark") {
          field(
            node,
            "美元指数基准",
            s.params[key],
            (value) => {
              s.params[key] = value;
              schedulePreview();
            },
            benchmarkChoices().map((a) => [a.id, a.label]),
          );
          continue;
        }
        if (key === "range") {
          field(
            node,
            "振幅口径",
            s.params[key],
            (value) => {
              s.params[key] = value;
              schedulePreview();
            },
            [
              ["true_range", "真实振幅"],
              ["high_low", "最高价减最低价"],
            ],
          );
          continue;
        }
        const factor = ["stddev", "multiple"].includes(key);
        const input = field(
          node,
          key === "period" && s.kind === "trailing_exit"
            ? "ATR 周期"
            : labels[key] || key,
          s.params[key],
          (value) => {
            s.params[key] = Number(value);
            schedulePreview();
          },
        );
        input.type = "number";
        input.min = factor || key === "threshold" ? "0.1" : "2";
        input.max = factor ? "10" : key === "threshold" ? "99.9" : "250";
        input.step = factor || key === "threshold" ? "0.1" : "1";
      }
    node.append(button("加入 / 更新监测", () => monitor(id)));
    openDialog("indicator-settings");
  }
  const kindNames = {
    macd: "MACD",
    ma: "双均线",
    boll_break: "布林突破",
    boll_return: "布林回归",
    donchian: "唐奇安通道",
    pullback: "短线回调",
    trailing_exit: "ATR 跟踪退出",
    squeeze: "波动收缩释放",
    relative_strength: "相对基准转强",
  };
  function describeRule(rule) {
    return `${rule.name} · ${kindNames[rule.kind]} · ${describeParams(rule.params)}`;
  }
  function describeParams(params, keys = Object.keys(params)) {
    return [
      ["fast", "快线"],
      ["slow", "慢线"],
      ["signal", "信号线"],
      ["period", "周期"],
      ["stddev", "标准差倍数"],
      ["trend", "趋势均线"],
      ["recovery", "修复均线"],
      ["rsi", "RSI 周期"],
      ["threshold", "RSI 阈值"],
      ["entry", "突破周期"],
      ["exit", "退出周期"],
      ["multiple", "通道倍数"],
      ["range", "振幅口径"],
      ["relative_period", "比值均线周期"],
      ["benchmark", "基准"],
    ]
      .filter(([key]) => keys.includes(key))
      .map(
        ([key, label]) =>
          `${label} ${key === "range" ? (params[key] === "true_range" ? "真实振幅" : "高低价差") : params[key]}`,
      )
      .join(" / ");
  }
  function matchesConfiguration(a, b) {
    if (a.name !== b.name || a.kind !== b.kind) return false;
    if (a.kind === "combination")
      return (
        a.trigger === b.trigger &&
        (a.filter || "") === (b.filter || "") &&
        (!a.filter || a.band === b.band)
      );
    return sameBaseRule(a, b);
  }
  function benchmarkChoices() {
    const current = config.assets.find((a) => a.id === selected);
    if (current?.provider !== "yahoo") return [];
    return config.assets.filter(
      (a) => a.provider === "yahoo" && a.code !== current.code,
    );
  }
  function renderCatalog() {
    const dialog = $("indicator-dialog");
    const catalog = dialog.querySelector(".indicator-catalog");
    catalog.replaceChildren();
    const add = (strategy) => {
      if (!selected) return status("请先选择标的", true);
      const index = draft.findIndex((entry) => entry.id === strategy.id);
      if (index < 0) draft.push(strategy);
      else draft[index] = strategy;
      schedulePreview();
      dialog.close();
    };
    const benchmark = benchmarkChoices()[0];
    for (const [kind, name, meaning, params] of [
      ["macd", "MACD", "副图", { fast: 12, slow: 26, signal: 9 }],
      ["ma", "双均线", "主图", { fast: 20, slow: 60 }],
      ["boll_break", "布林突破", "突破轨道", { period: 20, stddev: 2 }],
      ["boll_return", "布林回归", "从轨外回到轨内", { period: 20, stddev: 2 }],
      [
        "pullback",
        "短线回调",
        "长期上行",
        { trend: 200, recovery: 5, rsi: 2, threshold: 10 },
      ],
      ["donchian", "唐奇安通道", "突破与退出", { entry: 20, exit: 10 }],
      [
        "squeeze",
        "波动收缩释放",
        "肯特纳通道",
        { period: 20, stddev: 2, multiple: 1.5, range: "true_range" },
      ],
      [
        "trailing_exit",
        "ATR 跟踪退出",
        "突破后跟踪",
        { entry: 20, period: 22, multiple: 3 },
      ],
      ...(benchmark
        ? [
            [
              "relative_strength",
              "相对基准转强",
              "同一交易日收盘",
              { trend: 200, relative_period: 60, benchmark: benchmark.id },
            ],
          ]
        : []),
    ]) {
      const row = document.createElement("section");
      row.append(
        text("strong", `${name} · ${meaning} · ${describeParams(params)}`),
        button(`${name} ＋`, () =>
          add({
            id: `explore_${Date.now()}_${draft.length}`,
            name,
            kind,
            group: "探索",
            params: { ...params },
          }),
        ),
      );
      const saved = [];
      for (const strategy of config.strategies) {
        if (
          strategy.kind === kind &&
          !saved.some((entry) => matchesConfiguration(entry, strategy))
        )
          saved.push(strategy);
      }
      if (saved.length) {
        const details = document.createElement("details");
        details.append(text("summary", "已存配置"));
        for (const strategy of saved) {
          const keys = Object.keys(params);
          const values = describeParams(strategy.params, keys);
          const matching = keys.every(
            (key) => strategy.params[key] === params[key],
          )
            ? " · 与默认参数相同"
            : "";
          details.append(
            button(`${strategy.name} · ${values}${matching}`, () =>
              add({ ...clone(strategy), origin_id: strategy.id }),
            ),
          );
        }
        row.append(details);
      }
      catalog.append(row);
    }
    const combinations = document.createElement("section");
    const savedCombinations = [];
    for (const strategy of config.strategies) {
      if (
        strategy.kind === "combination" &&
        !savedCombinations.some((entry) =>
          matchesConfiguration(entry, strategy),
        )
      ) {
        savedCombinations.push(strategy);
      }
    }
    if (savedCombinations.length) {
      const details = document.createElement("details");
      details.append(text("summary", "已存组合策略"));
      for (const strategy of savedCombinations) {
        const trigger = config.strategies.find(
          (rule) => rule.id === strategy.trigger,
        );
        const band = config.strategies.find(
          (rule) => rule.id === strategy.band,
        );
        const filter =
          strategy.filter && band
            ? `${strategy.filter === "above_mid" ? "高于" : "低于"} ${describeRule(band)} 中轨`
            : "无过滤";
        details.append(
          button(
            `${strategy.name} · ${describeRule(trigger)} · ${filter}`,
            () =>
              add({
                ...clone(strategy),
                origin_id: strategy.id,
                dependencies: clone(
                  Object.fromEntries(
                    [trigger, ...(strategy.filter && band ? [band] : [])].map(
                      (rule) => [rule.id, rule],
                    ),
                  ),
                ),
              }),
          ),
        );
      }
      combinations.append(details);
    }
    combinations.append(
      button("创建组合策略…", () => {
        if (!selected) return status("请先选择标的", true);
        const available = [...config.strategies, ...draft];
        const trigger = available.find((s) => s.kind !== "combination"),
          band = available.find((s) => s.kind.startsWith("boll"));
        if (!trigger) return dialogStatus(dialog, "请先保存基础策略", true);
        draft.push({
          id: `explore_${Date.now()}`,
          name: "组合观察",
          kind: "combination",
          group: "组合",
          trigger: trigger.id,
          filter: band ? "above_mid" : "",
          band: band?.id,
        });
        renderIndicators();
        schedulePreview();
        $("indicator-dialog").close();
        editIndicator(draft.at(-1).id);
      }),
    );
    catalog.append(combinations);
  }
  async function mutate(payload, dialog) {
    if (configurationBusy) throw Error("配置正在保存，请稍候");
    configurationBusy = true;
    if (dialog) dialogStatus(dialog, "正在验证并保存…");
    try {
      const result = await request("/api/workspace/edit", {
        ...payload,
        version: config.version,
      });
      config = result.configuration;
      renderWatchlist();
      renderCatalog();
      if (dialog)
        dialogStatus(
          dialog,
          `配置 v${config.version} 已保存；监测下次检查生效。`,
        );
      status(
        `配置 v${config.version} 已保存 · 下一次检查生效 · 当前图表仍是快照`,
      );
      return result;
    } catch (error) {
      if (dialog) dialogStatus(dialog, error.message, true);
      status(error.message, true);
      throw error;
    } finally {
      configurationBusy = false;
    }
  }
  function monitor(id) {
    const s = draft.find((x) => x.id === id);
    if (!s) return;
    const dialog = $("monitor-dialog");
    let content = $("monitor-content");
    if (!content) {
      content = document.createElement("div");
      content.id = "monitor-content";
      dialog.insertBefore(content, dialog.querySelector(".dialog-status"));
    }
    content.replaceChildren();
    const asset = config.assets.find((a) => a.id === selected);
    if (!asset || asset.archived) {
      content.append(
        text("p", "该标的已归档或仅存在于历史报告。请先在观察列表中启用。"),
      );
      openDialog("monitor-dialog");
      return;
    }
    const originalId = s.origin_id || s.id;
    const shared = config.strategies.find((x) => x.id === originalId);
    const affected = config.assets.filter((a) =>
      a.strategies.includes(originalId),
    );
    content.append(
      text("p", `标的：${asset.label} · 规则：${s.name}`),
      text(
        "pre",
        JSON.stringify(
          s.params || {
            trigger: s.trigger,
            filter: s.filter,
            band: s.band,
          },
          null,
          2,
        ),
      ),
    );
    const dependencies = expandDependencies([s]).filter(
      (entry) => entry.id !== s.id,
    );
    const canReuse = dependencies.every((dependency) => {
      const current = config.strategies.find(
        (entry) => entry.id === dependency.id,
      );
      return current && sameBaseRule(dependency, current);
    });
    const candidates = canReuse
      ? config.strategies.filter((entry) => matchesConfiguration(s, entry))
      : [];
    const matching =
      candidates.find((entry) => asset.strategies.includes(entry.id)) ||
      candidates.find((entry) => entry.id === originalId) ||
      candidates[0];
    const scope = document.createElement("select");
    scope.setAttribute("aria-label", "监测更新范围");
    const own = text("option", "仅当前标的（复制为独立配置）");
    own.value = "private";
    if (matching) {
      const reuse = text(
        "option",
        `复用已有匹配项 · ${matching.name}（${matching.id}）`,
      );
      reuse.value = "reuse";
      scope.append(reuse);
    } else {
      scope.append(own);
    }
    if (shared && !matching) {
      const global = text("option", "更新共享配置");
      global.value = "shared";
      scope.append(global);
    }
    content.append(scope);
    const impact = text(
      "p",
      matching
        ? "将为当前标的复用匹配配置。"
        : "默认仅修改当前标的，其他标的配置保留。",
      "muted",
    );
    content.append(impact);
    scope.addEventListener("change", () => {
      impact.textContent =
        scope.value === "shared"
          ? `将更新共享配置，影响：${affected.map((a) => a.label).join("、") || "暂无已绑定标的"}；并为当前标的启用。组合的依赖使用当前配置；保留历史参数请选择仅当前标的。`
          : "默认仅修改当前标的，其他标的配置保留。";
    });
    content.append(
      button(matching ? "复用已有配置" : "保存监测规则", async () => {
        const strategy = clone(s);
        strategy.id = shared?.id || strategy.id;
        delete strategy.origin_id;
        delete strategy.dependencies;
        try {
          const result = await mutate(
            {
              action: "monitor",
              asset_id: selected,
              strategy,
              dependencies: Object.fromEntries(
                dependencies.map((d) => [d.id, d]),
              ),
              shared: scope.value === "shared",
              reuse_id: matching?.id,
              replace_id: shared?.id,
            },
            dialog,
          );
          if (result.strategy_id) s.origin_id = result.strategy_id;
          saveDraft();
          renderIndicators();
        } catch {
          /* message is displayed in the dialog */
        }
      }),
    );
    openDialog("monitor-dialog");
  }
  async function renderSymbols() {
    const query = $("symbol-query").value;
    try {
      const result = await request(
        `/api/symbols?q=${encodeURIComponent(query)}`,
      );
      if (query !== $("symbol-query").value) return;
      const node = $("symbol-results");
      node.replaceChildren();
      for (const a of result.assets) {
        const row = text("div", "", "symbol-row");
        row.append(
          text("strong", a.label),
          text(
            "small",
            `${a.code} · ${a.market || a.session_profile} · ${a.provider}`,
          ),
          button("选择 / 加入", async () => {
            try {
              const existing = config.assets.find((x) => x.id === a.id);
              let result;
              if (
                !existing ||
                existing.archived ||
                existing.watchlisted === false
              )
                result = await mutate(
                  { action: "add_asset", asset: a },
                  $("symbols-dialog"),
                );
              const id = result?.selected || a.id;
              if (result?.quote) {
                cache.set(id, result.quote);
              }
              $("symbols-dialog").close();
              await choose(id);
            } catch {
              /* validation failure remains visible */
            }
          }),
        );
        node.append(row);
      }
      if (!result.assets.length)
        node.append(text("p", "目录中未找到。可在下方展开手动添加。"));
    } catch (error) {
      dialogStatus($("symbols-dialog"), error.message, true);
    }
  }
  $("symbol-query").addEventListener("input", renderSymbols);
  document
    .querySelectorAll('[data-open="symbols-dialog"]')
    .forEach((node) => node.addEventListener("click", renderSymbols));
  $("manual-asset-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    const [provider, session_profile] = data.profile.split(":");
    const asset = {
      ...data,
      provider,
      session_profile,
      market:
        session_profile === "us_equity"
          ? "美国指数"
          : session_profile === "sge_gold"
            ? "上海黄金交易所"
            : "中国指数",
      currency: session_profile === "sge_gold" ? "元/克" : "点",
    };
    try {
      const result = await mutate(
        { action: "add_asset", asset },
        $("symbols-dialog"),
      );
      if (result.quote) cache.set(result.selected, result.quote);
      $("symbols-dialog").close();
      await choose(result.selected);
    } catch {
      /* validation displayed */
    }
  });
  async function choose(id, date) {
    const selection = selectAsset(id, { reportOnly: Boolean(date) });
    if (compactWatchlist.matches) setWatchlistHidden(true);
    await selection;
    if (date && selected === id) chart.focusEvent(date);
  }
  function renderWatchlist() {
    $("workspace-configuration").textContent =
      `当前监测 v${config.version} · 本报告配置 ${boot.configuration_version ? "v" + boot.configuration_version : "历史未记录"}${boot.configuration_version && boot.configuration_version !== config.version ? " · 新规则下次检查生效" : ""}`;
    renderAssets(
      $("watchlist-items"),
      $("watchlist-filter").value.toLowerCase(),
      config.assets,
      selected,
      originals,
      $("watchlist-view").value,
      {
        text,
        button,
        choose,
        assetSettings,
        reorder,
        update: updateWatchlistAsset,
      },
    );
  }
  $("watchlist-filter").addEventListener("input", () => {
    if (config) renderWatchlist();
  });
  $("watchlist-view").addEventListener("change", () => {
    if (config) renderWatchlist();
  });
  async function updateWatchlistAsset(id, fields) {
    try {
      await mutate({ action: "asset", asset_id: id, ...fields });
    } catch {
      /* shown */
    }
  }
  async function reorder(id, delta) {
    const ids = reorderAssets(config.assets, id, delta);
    if (!ids) return;
    try {
      await mutate({ action: "order", ids });
    } catch {
      /* shown */
    }
  }
  function assetSettings(id) {
    editingAsset = id;
    const asset = config.assets.find((a) => a.id === id),
      form = $("asset-settings-form");
    form.elements.label.value = asset.label;
    form.elements.group.value = asset.group || "";
    form.elements.state.value = asset.archived
      ? "archived"
      : asset.enabled
        ? "enabled"
        : "disabled";
    const monitors = $("asset-monitors");
    monitors.replaceChildren();
    monitors.append(text("h3", "已配置监测 · 下一次检查使用"));
    for (const sid of asset.strategies) {
      const s = config.strategies.find((x) => x.id === sid);
      if (!s) continue;
      const row = text("div", "", "symbol-row");
      row.append(
        text("strong", s.name),
        button("移除监测", async () => {
          try {
            await mutate(
              { action: "unmonitor", asset_id: id, strategy_id: sid },
              $("asset-settings"),
            );
            assetSettings(id);
          } catch {
            /* shown */
          }
        }),
      );
      monitors.append(row);
    }
    openDialog("asset-settings");
  }
  $("asset-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const result = await mutate(
        {
          action: "asset",
          asset_id: editingAsset,
          label: data.label,
          group: data.group,
          enabled: data.state === "enabled",
          archived: data.state === "archived",
        },
        $("asset-settings"),
      );
      if (result.quote) cache.set(editingAsset, result.quote);
    } catch {
      /* shown */
    }
  });
  function allTemplates() {
    return [
      {
        template_id: "builtin_trend",
        name: "MACD + 布林带",
        strategies: [
          {
            id: "template_macd",
            name: "MACD 12/26/9",
            kind: "macd",
            group: "趋势",
            params: { fast: 12, slow: 26, signal: 9 },
          },
          {
            id: "template_boll",
            name: "布林带 20/2",
            kind: "boll_break",
            group: "布林带",
            params: { period: 20, stddev: 2 },
          },
        ],
      },
    ].concat(templateList);
  }
  function renderTemplates() {
    const node = $("template-list");
    node.replaceChildren();
    for (const t of allTemplates()) {
      const row = text("div", "", "symbol-row");
      row.append(
        text("strong", t.name),
        text(
          "small",
          `${t.strategies.length} 个指标${t.id ? ` · 版本 ${t.id}` : " · 预置"}`,
        ),
        button("应用到图表", () => {
          if (!selected)
            return dialogStatus($("templates-dialog"), "请先选择标的", true);
          draft = clone(t.strategies);
          activeTemplate = t.template_id;
          state.template = t.template_id;
          persist();
          renderIndicators();
          schedulePreview();
          $("template-name").value = t.name;
          $("update-template").disabled = !t.id;
          $("templates-dialog").close();
        }),
      );
      node.append(row);
    }
  }
  async function storeTemplate(overwrite = false) {
    const old = templateList.find((t) => t.template_id === activeTemplate);
    try {
      const result = await request("/api/workspace/templates", {
        name: $("template-name").value,
        strategies: expandDependencies(draft),
        ...(overwrite && old
          ? { template_id: old.template_id, expected: old.id }
          : {}),
      });
      templateList = result.templates;
      const t = templateList[0];
      activeTemplate = t.template_id;
      state.template = activeTemplate;
      saveDraft();
      renderTemplates();
      $("update-template").disabled = false;
      dialogStatus(
        $("templates-dialog"),
        "模板已保存；未修改任何标的监测规则。",
      );
    } catch (error) {
      dialogStatus($("templates-dialog"), error.message, true);
    }
  }
  $("template-form").addEventListener("submit", (event) => {
    event.preventDefault();
    storeTemplate();
  });
  $("update-template").addEventListener("click", () => storeTemplate(true));
  $("restore-report").addEventListener("click", () => {
    if (!selected) return;
    clearTimeout(previewTimer);
    ++previewSequence;
    currentPreview = null;
    exploring = false;
    draft = reportStrategies(selected);
    activeTemplate = null;
    delete state.template;
    try {
      localStorage.removeItem(draftKey(selected));
    } catch {}
    if (originals.has(selected))
      chart.show(clone(originals.get(selected)), {
        view: state.views[assetViewKey(selected)],
      });
    $("exploration-panel").hidden = true;
    setMode(false);
    renderIndicators();
    restoreLayers();
    document.querySelectorAll(".decision-panel form").forEach((form) => {
      if (form.querySelector('[name="asset_id"]').value === selected)
        form.elements.basis_choice.value = "report";
    });
    updateBasis();
    persist();
    status("已还原当时报告；自动监测配置保持当前已保存版本");
  });
  const compactWatchlist = matchMedia("(max-width: 720px)");
  const watchlistToggle = $("toggle-watchlist");
  function setWatchlistHidden(hidden) {
    document.body.classList.toggle("watchlist-hidden", hidden);
    watchlistToggle.setAttribute("aria-expanded", String(!hidden));
  }
  function resetWatchlist() {
    setWatchlistHidden(
      compactWatchlist.matches || Boolean(state.watchlistHidden),
    );
  }
  compactWatchlist.addEventListener("change", resetWatchlist);
  resetWatchlist();
  watchlistToggle.addEventListener("click", () => {
    const hidden = watchlistToggle.getAttribute("aria-expanded") === "true";
    setWatchlistHidden(hidden);
    if (!compactWatchlist.matches) {
      state.watchlistHidden = hidden;
      persist();
    }
  });
  let nextSignal = 0;
  $("new-signals")?.addEventListener("click", () => {
    const signals = [...document.querySelectorAll("[data-new-signal]")];
    const signal = signals[nextSignal++ % signals.length];
    if (!signal) return;
    selectAsset(signal.closest("[data-detail]").dataset.detail);
    signal.closest('details[data-layout="signals"]').open = true;
    signal.focus();
  });
  document.querySelectorAll("[data-layout]").forEach((panel) => {
    if (panel.dataset.layout in state.layout)
      panel.open = state.layout[panel.dataset.layout];
    panel.addEventListener("toggle", () => {
      state.layout[panel.dataset.layout] = panel.open;
      persist();
    });
  });
  $("journal-record-decision")?.addEventListener("click", () => {
    const feedback = $("journal-record-status");
    const note = document.querySelector(
      '[data-detail]:not([hidden]) textarea[name="note"]',
    );
    if (!note) {
      feedback.textContent = "当前标的没有正式报告，无法记录判断。";
      return;
    }
    feedback.textContent = "";
    note.closest("details").open = true;
    note.focus();
  });
  document.querySelectorAll(".decision-panel form").forEach((form) => {
    const assetId = form.querySelector('[name="asset_id"]').value;
    const key = `chanceTrack.decision.${boot.report_id}.${assetId}`;
    const saved = read(key, null);
    if (saved)
      for (const name of ["note", "action", "execution", "basis_choice"]) {
        if (saved[name] != null) form.elements[name].value = saved[name];
      }
    function remember() {
      const data = Object.fromEntries(new FormData(form));
      write(key, {
        note: data.note,
        action: data.action,
        execution: data.execution,
        basis_choice: data.basis_choice,
      });
    }
    form.addEventListener("input", remember);
    form.addEventListener("change", () => {
      updateBasis();
      remember();
    });
    if (saved) form.dispatchEvent(new Event("input", { bubbles: true }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      updateBasis();
      if (
        form.elements.basis_choice.value === "exploration" &&
        !form.querySelector("[data-exploration-basis]").value
      ) {
        status("探索计算尚未完成，暂不能作为决策依据", true);
        form.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
      const submit = form.querySelector('button[type="submit"], .save-button');
      if (submit) submit.disabled = true;
      try {
        const response = await fetch(form.getAttribute("action"), {
          method: "POST",
          body: new FormData(form),
        });
        if (!response.ok) {
          const data = await response.json();
          throw Error(data.detail || "保存失败，请重试");
        }
        try {
          localStorage.removeItem(key);
        } catch {}
        state.layout.journal = true;
        persist();
        window.location.assign(response.url);
      } catch (error) {
        status(error.message, true);
        form.dispatchEvent(new Event("input", { bubbles: true }));
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  });
  async function initialize() {
    try {
      const result = await request("/api/workspace");
      config = result.configuration;
      templateList = result.templates;
      renderWatchlist();
      renderCatalog();
      renderTemplates();
      const available = config.assets.filter((a) => !a.archived);
      const initial = boot.historical
        ? chart?.current()?.asset.id || available[0]?.id
        : available.some((a) => a.id === state.selected)
          ? state.selected
          : chart?.current()?.asset.id || available[0]?.id;
      if (initial) await selectAsset(initial);
    } catch (error) {
      status(error.message, true);
    }
  }
  // Reload only configuration when another tab changes it; drafts and the chart stay in place.
  window.addEventListener("focus", async () => {
    if (!config || configurationBusy) return;
    try {
      const result = await request("/api/workspace");
      config = result.configuration;
      templateList = result.templates;
      renderWatchlist();
      renderCatalog();
      renderTemplates();
    } catch {
      /* next explicit action surfaces the error */
    }
  });
  initialize();
})();

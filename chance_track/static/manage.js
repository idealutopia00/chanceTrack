(() => {
  const config = JSON.parse(
    document.getElementById("management-data").textContent,
  );
  const assets = document.getElementById("asset-editor"),
    strategies = document.getElementById("strategy-editor");
  const kinds = [
    ["macd", "MACD交叉"],
    ["ma", "双均线交叉"],
    ["boll_break", "布林突破"],
    ["boll_return", "布林回归"],
    ["donchian", "唐奇安突破与退出"],
    ["pullback", "长期上行中的短线回调"],
    ["trailing_exit", "突破后的 ATR 跟踪退出"],
    ["squeeze", "波动收缩后的方向释放"],
    ["relative_strength", "相对基准转强"],
    ["combination", "触发 + 过滤"],
  ];
  const kindLabels = new Map(kinds);
  const draftCopies = new Set(config.draft_copies);
  function references(snapshot, id) {
    return [
      ...snapshot.assets
        .filter((asset) => asset.strategies?.includes(id))
        .map((asset) => `标的：${asset.label}（${asset.id}）`),
      ...snapshot.strategies
        .filter((rule) => rule.kind === "combination" &&
          (rule.trigger === id || rule.band === id))
        .map((rule) => `组合策略：${rule.name}（${rule.id}）`),
    ];
  }
  const lifecycle = document.getElementById("strategy-lifecycle");
  if (lifecycle) {
    const saved = structuredClone(config);
    const source = lifecycle.elements.source;
    const keep = lifecycle.elements.keep;
    const preview = document.getElementById("strategy-lifecycle-preview");
    const submit = lifecycle.querySelector('[type="submit"]');
    source.append(new Option("请选择", ""));
    keep.append(new Option("删除来源（仅限无引用）", ""));
    for (const rule of saved.strategies) {
      const label = `${rule.name}（${rule.id}）`;
      source.append(new Option(label, rule.id));
      keep.append(new Option(label, rule.id));
    }
    function summarize(rule) {
      const kind = kindLabels.get(rule.kind) ?? rule.kind;
      const settings = rule.kind === "combination"
        ? { trigger: rule.trigger, filter: rule.filter, band: rule.band }
        : rule.params;
      return `${rule.name}（${rule.id}） · ${kind} · ${JSON.stringify(settings)}`;
    }
    function updateLifecycle() {
      preview.replaceChildren();
      submit.disabled = true;
      const from = saved.strategies.find((rule) => rule.id === source.value);
      const to = saved.strategies.find((rule) => rule.id === keep.value);
      if (!from) return;
      const usage = references(saved, from.id);
      const descriptions = [
        `来源：${summarize(from)}`,
        to ? `保留：${summarize(to)}` : "操作：删除来源配置",
        `来源引用：${usage.join("、") || "无"}`,
      ];
      if (to) {
        const targetUsage = references(saved, to.id);
        descriptions.push(`保留项引用：${targetUsage.join("、") || "无"}`);
      }
      if (!to && usage.length) {
        descriptions.push("请先替换或解除上述引用。");
      } else if (from === to) {
        descriptions.push("请选择不同的保留配置。");
      } else {
        submit.disabled = false;
      }
      for (const text of descriptions) {
        const line = document.createElement("p");
        line.textContent = text;
        preview.append(line);
      }
    }
    lifecycle.addEventListener("change", updateLifecycle);
    lifecycle.addEventListener("reset", () => {
      preview.replaceChildren();
      submit.disabled = true;
    });
  }
  function field(parent, label, object, key, options) {
    const wrapper = document.createElement("label");
    wrapper.className = "field-label";
    wrapper.textContent = label;
    const input = document.createElement(options ? "select" : "input");
    if (options)
      for (const [value, text] of options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      }
    input.value = object[key] ?? "";
    input.addEventListener("change", () => {
      object[key] = input.value;
    });
    wrapper.append(input);
    parent.append(wrapper);
    return input;
  }
  function render() {
    assets.replaceChildren();
    strategies.replaceChildren();
    for (const a of config.assets) {
      const card = document.createElement("fieldset");
      const title = document.createElement("legend");
      title.textContent = a.label || "新增标的";
      card.append(title);
      for (const [key, label] of [
        ["id", "唯一ID"],
        ["label", "显示名称"],
        ["code", "行情代码"],
        ["ticker", "简称"],
        ["market", "市场说明"],
        ["currency", "单位"],
        ["secid", "东方财富 secid"],
        ["expected_name", "东方财富精确名称"],
      ])
        field(card, label, a, key);
      field(card, "数据源", a, "provider", [
        ["yahoo", "Yahoo 指数"],
        ["eastmoney", "东方财富指数 / 黄金"],
      ]);
      field(card, "市场时段", a, "session_profile", [
        ["us_equity", "美国指数 · 纽约时区"],
        ["cn_equity", "中国指数 · 上海时区"],
        ["sge_gold", "上金所黄金 · 上海时区"],
      ]);
      const state = {
        value: a.archived ? "archived" : a.enabled ? "enabled" : "disabled",
      };
      const select = field(card, "状态", state, "value", [
        ["enabled", "启用"],
        ["disabled", "停用"],
        ["archived", "归档"],
      ]);
      select.addEventListener("change", () => {
        a.archived = state.value === "archived";
        a.enabled = state.value === "enabled";
      });
      const bindings = document.createElement("div");
      bindings.textContent = "绑定策略：";
      for (const s of config.strategies) {
        const label = document.createElement("label"),
          input = document.createElement("input");
        input.type = "checkbox";
        input.checked = (a.strategies || []).includes(s.id);
        input.addEventListener("change", () => {
          a.strategies = (a.strategies || []).filter((id) => id !== s.id);
          if (input.checked) a.strategies.push(s.id);
        });
        label.append(input, document.createTextNode(s.name));
        bindings.append(label);
      }
      card.append(bindings);
      assets.append(card);
    }
    const configurations = [...config.strategies].sort((a, b) =>
      a.kind.localeCompare(b.kind),
    );
    let previousKind;
    for (const s of configurations) {
      if (s.kind !== previousKind) {
        const heading = document.createElement("h3");
        heading.textContent = kindLabels.get(s.kind) ?? s.kind;
        strategies.append(heading);
        previousKind = s.kind;
      }
      const card = document.createElement("fieldset");
      card.id = `strategy-${s.id}`;
      const title = document.createElement("legend");
      title.textContent = s.name;
      card.append(title);
      const id = field(card, "唯一ID", s, "id");
      id.readOnly = true;
      field(card, "配置名称", s, "name");
      field(card, "展示分组", s, "group");
      const kind = field(card, "规则", s, "kind", kinds);
      kind.addEventListener("change", () => {
        const defaults = {
          macd: { fast: 12, slow: 26, signal: 9 },
          ma: { fast: 20, slow: 60 },
          donchian: { entry: 20, exit: 10 },
          pullback: { trend: 200, recovery: 5, rsi: 2, threshold: 10 },
          trailing_exit: { entry: 20, period: 22, multiple: 3 },
          squeeze: {
            period: 20,
            stddev: 2,
            multiple: 1.5,
            range: "true_range",
          },
        };
        s.params = defaults[s.kind] || { period: 20, stddev: 2 };
        render();
      });
      if (s.kind === "combination") {
        const options = config.strategies
          .filter((x) => x.kind !== "combination")
          .map((x) => [x.id, x.name]);
        field(card, "触发配置", s, "trigger", [["", "请选择"], ...options]);
        field(card, "过滤条件", s, "filter", [
          ["", "无过滤"],
          ["above_mid", "收盘价高于布林中轨"],
          ["below_mid", "收盘价低于布林中轨"],
        ]);
        field(card, "布林过滤配置", s, "band", [
          ["", "请选择"],
          ...config.strategies
            .filter((x) => x.kind.startsWith("boll"))
            .map((x) => [x.id, x.name]),
        ]);
      } else
        for (const key of Object.keys(s.params)) {
          if (key === "benchmark") {
            field(card, "美元指数基准", s.params, key, [
              ["", "请选择"],
              ...config.assets
                .filter((a) => a.provider === "yahoo")
                .map((a) => [a.id, a.label]),
            ]);
            continue;
          }
          if (key === "range") {
            field(card, "振幅口径", s.params, key, [
              ["true_range", "真实振幅"],
              ["high_low", "最高价减最低价"],
            ]);
            continue;
          }
          const input = field(card, key, s.params, key);
          input.type = "number";
          input.step = ["stddev", "multiple", "threshold"].includes(key)
            ? "0.1"
            : "1";
          input.addEventListener("change", () => {
            s.params[key] = Number(input.value);
          });
        }
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "复制为新配置";
      copy.onclick = () => {
        const clone = structuredClone(s);
        clone.id = `strategy_${Date.now()}`;
        clone.name += " 副本";
        delete clone.revision;
        config.strategies.push(clone);
        draftCopies.add(clone.id);
        render();
      };
      card.append(copy);

      if (draftCopies.has(s.id)) {
        const undo = document.createElement("button");
        undo.type = "button";
        undo.textContent = "撤销复制";
        undo.onclick = () => {
          const usage = references(config, s.id);
          if (usage.length) {
            document.getElementById("save-status").textContent =
              `请先解除草稿引用：${usage.join("、")}`;
            return;
          }
          config.strategies = config.strategies.filter((rule) => rule.id !== s.id);
          draftCopies.delete(s.id);
          render();
        };
        card.append(undo);
      }

      const usage = document.createElement("p");
      const monitored = config.assets
        .filter(
          (asset) => asset.enabled && !asset.archived && asset.strategies?.includes(s.id),
        )
        .map((asset) => asset.label);
      usage.textContent = `监测标的：${monitored.join("、") || "暂无"}`;
      card.append(usage);
      strategies.append(card);
    }
  }
  document.getElementById("add-asset").onclick = () => {
    config.assets.push({
      id: `asset_${Date.now()}`,
      label: "新标的",
      code: "",
      provider: "yahoo",
      session_profile: "us_equity",
      kind: "index",
      enabled: false,
      strategies: [],
    });
    render();
  };
  document.getElementById("add-strategy").onclick = () => {
    config.strategies.push({
      id: `strategy_${Date.now()}`,
      name: "新策略",
      group: "未分组",
      kind: "macd",
      params: { fast: 12, slow: 26, signal: 9 },
    });
    render();
  };
  document.getElementById("management-form").onsubmit = () => {
    config.draft_copies = [...draftCopies];
    document.getElementById("config-payload").value = JSON.stringify(config);
    document.getElementById("save-status").textContent =
      "正在校验配置及行情，请稍候…";
  };
  const reuse = document.getElementById("reuse-strategy");
  reuse?.addEventListener("click", () => {
    const { duplicate, match, equivalents } = reuse.dataset;
    const equivalentIds = new Set(equivalents.split(" "));
    const replace = (id) => (id === duplicate ? match : id);
    for (const asset of config.assets) {
      const bindings = asset.strategies || [];
      const bound = bindings.find((id) => equivalentIds.has(id));
      asset.strategies = [
        ...new Set(
          bindings.map((id) => (id === duplicate ? bound || match : id)),
        ),
      ];
    }
    config.strategies = config.strategies.filter((s) => s.id !== duplicate);
    for (const strategy of config.strategies) {
      if (strategy.kind !== "combination") continue;
      strategy.trigger = replace(strategy.trigger);
      strategy.band = replace(strategy.band);
    }
    reuse.hidden = true;
    render();
    document.getElementById(`strategy-${match}`).scrollIntoView();
    document.getElementById("save-status").textContent =
      "已在草稿中复用配置，请保存新版本。";
  });
  render();
})();

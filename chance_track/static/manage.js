(() => {
  const config = JSON.parse(
    document.getElementById("management-data").textContent,
  );
  const assets = document.getElementById("asset-editor"),
    strategies = document.getElementById("strategy-editor");
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
    for (const s of config.strategies) {
      const card = document.createElement("fieldset");
      const title = document.createElement("legend");
      title.textContent = s.name;
      card.append(title);
      const id = field(card, "唯一ID", s, "id");
      id.readOnly = true;
      field(card, "配置名称", s, "name");
      field(card, "展示分组", s, "group");
      const kind = field(card, "规则", s, "kind", [
        ["macd", "MACD交叉"],
        ["ma", "双均线交叉"],
        ["boll_break", "布林突破"],
        ["boll_return", "布林回归"],
        ["combination", "触发 + 过滤"],
      ]);
      kind.addEventListener("change", () => {
        s.params =
          s.kind === "macd"
            ? { fast: 12, slow: 26, signal: 9 }
            : s.kind === "ma"
              ? { fast: 20, slow: 60 }
              : { period: 20, stddev: 2 };
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
          const input = field(card, key, s.params, key);
          input.type = "number";
          input.step = key === "stddev" ? "0.1" : "1";
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
        render();
      };
      card.append(copy);
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
    document.getElementById("config-payload").value = JSON.stringify(config);
    document.getElementById("save-status").textContent =
      "正在校验配置及行情，请稍候…";
  };
  render();
})();

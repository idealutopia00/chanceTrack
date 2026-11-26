export function groupName(asset) {
  return (
    asset.group ||
    (asset.session_profile === "us_equity"
      ? "美国指数"
      : asset.session_profile === "sge_gold"
        ? "黄金"
        : "国内指数")
  );
}

export function reorderAssets(assets, id, delta) {
  const asset = assets.find((item) => item.id === id);
  if (!asset) return;
  const peers = assets.filter(
    (item) =>
      !item.archived &&
      item.watchlisted !== false &&
      groupName(item) === groupName(asset),
  );
  const target = peers[peers.indexOf(asset) + delta];
  if (!target) return;
  const ids = assets.map((item) => item.id);
  const from = ids.indexOf(id),
    to = ids.indexOf(target.id);
  [ids[from], ids[to]] = [ids[to], ids[from]];
  return ids;
}

export function renderWatchlist(
  node,
  filter,
  assets,
  selected,
  originals,
  view,
  actions,
) {
  const { text, button, choose, assetSettings, reorder, update } = actions;
  const groups = new Map();
  for (const asset of assets) {
    if (
      asset.archived ||
      asset.watchlisted === false ||
      !`${asset.label} ${asset.code} ${(asset.aliases || []).join(" ")}`
        .toLowerCase()
        .includes(filter)
    )
      continue;
    const item = originals.get(asset.id);
    const chart = item?.result?.chart;
    const days = new Set(
      (chart?.candles ?? []).slice(-5).map((bar) => bar.time),
    );
    const event = (chart?.strategy_events ?? chart?.crosses ?? [])
      .filter(
        (entry) =>
          days.has(entry.time) &&
          (view !== "pending" || entry.provisional === true),
      )
      .sort((a, b) => b.time.localeCompare(a.time))[0];
    if (
      (view === "priority" && !asset.priority) ||
      (["recent", "pending"].includes(view) && !event)
    )
      continue;
    const group = groupName(asset);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push({ asset, event, item });
  }
  const oldSelection = node.querySelector(".watchlist-row.active");
  const oldAsset = oldSelection?.dataset.assetId;
  const oldTop = oldSelection?.getBoundingClientRect().top;
  node.replaceChildren();
  for (const [group, members] of groups) {
    node.append(text("h4", group, "watchlist-group"));
    for (const { asset, event, item } of members) {
      const row = text(
        "div",
        "",
        `watchlist-row ${selected === asset.id ? "active" : ""}`,
      );
      row.dataset.assetId = asset.id;
      const main = button("", () => choose(asset.id), "watchlist-symbol");
      main.setAttribute("aria-pressed", String(selected === asset.id));
      const bars = item?.result?.chart?.candles ?? [];
      const close = bars.at(-1)?.close ?? item?.last_quote?.close;
      const previous = bars.at(-2)?.close;
      const change = previous && close != null ? close / previous - 1 : null;
      const identity = text("span", "", "watchlist-identity");
      identity.append(text("strong", asset.label), text("small", asset.code));
      const quote = text("span", "", "watchlist-quote");
      quote.append(
        text("strong", close == null ? "—" : close.toLocaleString("zh-CN", { maximumFractionDigits: 4 })),
        text(
          "small",
          item?.status === "error"
            ? "更新失败"
            : change == null
              ? "—"
              : new Intl.NumberFormat("zh-CN", {
                  style: "percent",
                  maximumFractionDigits: 2,
                  signDisplay: "exceptZero",
                }).format(change),
        ),
      );
      main.append(identity, quote);
      row.append(main);
      if (event) {
        const eventName =
          event.event ?? (event.kind === "golden" ? "金叉" : "死叉");
        row.append(
          button(
            `${eventName} · ${event.time}${event.provisional ? " · 待确认" : ""}`,
            () => choose(asset.id, event.time),
            "watchlist-event",
          ),
        );
      }
      const menu = document.createElement("details");
      menu.className = "watchlist-more";
      menu.append(text("summary", "更多"));
      const controls = text("div", "", "watchlist-actions");
      controls.append(
        button("设置", () => assetSettings(asset.id)),
        button("上移", () => reorder(asset.id, -1)),
        button("下移", () => reorder(asset.id, 1)),
        button(asset.priority ? "取消重点关注" : "设为重点关注", () =>
          update(asset.id, { priority: !asset.priority }),
        ),
        button("移出自选", () => update(asset.id, { watchlisted: false })),
      );
      if (asset.enabled)
        controls.append(
          button("停用监测", () => update(asset.id, { enabled: false })),
        );
      menu.append(controls);
      row.append(menu);
      node.append(row);
    }
  }
  const active = node.querySelector(".watchlist-row.active");
  if (active && oldAsset && oldAsset !== selected && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const offset = oldTop - active.getBoundingClientRect().top;
    if (offset) {
      active.style.setProperty("--watchlist-selection-offset", `${offset}px`);
      active.classList.add("selection-moving");
    }
  }
  if (!groups.size) node.append(text("p", "没有符合条件的标的。"));
}

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
    (item) => !item.archived && groupName(item) === groupName(asset),
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
  actions,
) {
  const { text, button, choose, assetSettings, reorder } = actions;
  const groups = new Map();
  for (const asset of assets) {
    if (
      asset.archived ||
      !`${asset.label} ${asset.code}`.toLowerCase().includes(filter)
    )
      continue;
    const group = groupName(asset);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(asset);
  }
  node.replaceChildren();
  for (const [group, members] of groups) {
    node.append(text("h4", group, "watchlist-group"));
    for (const asset of members) {
      const row = text(
        "div",
        "",
        `watchlist-row ${selected === asset.id ? "active" : ""}`,
      );
      const main = button("", () => choose(asset.id), "watchlist-symbol");
      main.setAttribute("aria-pressed", String(selected === asset.id));
      const item = originals.get(asset.id);
      const price = item?.result?.close ?? item?.last_quote?.close;
      main.append(
        text("strong", asset.label),
        text(
          "small",
          `${asset.ticker || asset.code} · ${asset.enabled ? `${asset.strategies.length} 个监测` : "已停用"}`,
        ),
        text(
          "span",
          price == null
            ? item?.status === "error"
              ? "无可用行情"
              : "加载行情"
            : price.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) +
                (item.status === "error" ? " · 本次更新失败" : ""),
        ),
      );
      const controls = text("div", "", "watchlist-actions");
      controls.append(
        button("⚙", () => assetSettings(asset.id)),
        button("↑", () => reorder(asset.id, -1)),
        button("↓", () => reorder(asset.id, 1)),
      );
      row.append(main, controls);
      node.append(row);
    }
  }
  if (!groups.size)
    node.append(text("p", "列表为空，可搜索添加或从目录恢复归档标的。"));
}

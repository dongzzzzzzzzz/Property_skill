---
name: property-c-skill
description: |
  房产 C 决策技能。用于把用户的找房需求映射成 `search_properties` / `compare_properties` 调用，
  再基于 OK 或未来接入平台的数据做推荐、不推荐和多维对比分析。
---

# property-c-skill

这个技能负责“找房决策”。它的职责不是罗列结果，而是：

- 把用户需求映射成正确的 `search_properties` 参数
- 用当前仓库的 workflow 调用 `ok-core-skill` 拿数据
- 输出推荐理由、不推荐理由、对比维度、关键字段缺失和下一步建议

这个技能只使用本仓库现有 CLI，不新增包装脚本，不直接操作底层平台页面。

## 可用命令

```bash
# 找房决策
python scripts/cli.py search_properties --provider ok --country singapore --city singapore --keyword "apartment" --budget-max 3500 --bedrooms 2 --rent-or-sale rent --feature furnished --max-results 100

# 比较房源
python scripts/cli.py compare_properties --provider ok --url "<url-1>" --url "<url-2>"

# 估算总成本
python scripts/cli.py estimate_total_cost --price 3200 --rent-or-sale rent --deposit-months 2 --parking-monthly 120 --commute-cost-per-trip 3

# 性价比
python scripts/cli.py score_value --provider ok --url "<listing-url>"

# 附近学校
python scripts/cli.py find_nearby_schools --provider ok --url "<listing-url>"
```

## 1.0 边界

- `GPS附近` 只有在 location 能解析成坐标时才做距离判断。
- `学区查询` 在 1.0 只落地为“附近学校查询”。
- `真实通勤 ETA` 不在 1.0 承诺范围内；没有路线服务时会降级为粗略估算。

## 调用规则

- 用户说“找房 / 推荐 / 帮我选 / 筛房 / 看哪套更合适”时，一律先调用 `search_properties`。
- 用户已经给了 2 套或多套 URL，要精细横比时才调用 `compare_properties`。
- 不要直接调用 `ok-core-skill` 的 `search` 或 `browse-category`；先走本 skill 的 `search_properties`。
- 决策型搜索默认就是大样本分析：
  - 默认 `--max-results 100`
  - 不要自己把样本量降到 `20`
  - 只有用户明确说“先快速看几个样本”时，才允许低样本
- 不要把 `--detail-limit` 暴露成上层常规参数；详情抓取由 workflow 内部分层控制。

## 意图映射

- `我想在 Melbourne Southbank 租 1 室，预算 A$1000/周以内`
  - `search_properties --country australia --city melbourne --area southbank --bedrooms 1 --rent-or-sale rent --budget-max 4330`
- `我想在 New York 市区买一套 3 室`
  - `search_properties --country usa --city new-york --bedrooms 3 --rent-or-sale sale --max-results 100`
- `帮我比较这 2 套`
  - `compare_properties --url "<url-1>" --url "<url-2>"`

## 输出使用规则

- `search_properties` 的返回里优先使用：
  - `decision_mode`
  - `decision_brief`
  - `render_ready_summary`
  - `must_show_findings`
  - `recommendation_cards_compact`
  - `why_these_listings`
  - `why_not_more`
  - `sample_basis_short`
  - `compare_matrix`
  - `field_status`
  - `known_fields`
  - `missing_fields`
- 如果上层会二次摘要，优先消费 compact 字段，不要从 `listings` 自己拼一张“标题/价格/区域”表。
- 不要直接把 `listings` 原样平铺给用户；`listings` 主要用于调试和上层二次处理。
- 推荐区只在 `decision_mode = recommend` 时展示，且优先使用 `recommended_listings[].decision_reason`、`recommended_listings[].why_not_ideal`、`recommended_listings[].url`、`recommended_listings[].primary_image_url`、`recommended_listings[].image_note`。
- 当 `decision_mode = watchlist` 时，只能把 `watchlist_candidates` 展示成“可参考观察项”，不能写成“推荐”。
- 当 `decision_mode = explain_only` 时，先展示 `result_judgement` 和 `query_fit_summary`，不要输出“首选/推荐度/星级”。
- 不要自己把“特点/卖点”改写成推荐理由；推荐理由必须优先复用结构化字段里的决策解释。
- 不要根据 `score` 自己生成“首选”或“Top 1”；必须先看 `decision_mode` 和 `decision_tag`。
- 缺失字段必须显式展示；`unknown` 只能解释为“当前页面没有足够信息支持判断”。
- `compare_matrix` 优先于自由描述；如果要解释推荐理由，必须引用它对价格、位置、图片质量和关键缺失字段的比较。
- `field_sources` 和 `decision_mode` 是上层消费的硬约束，不得忽略。
- 推荐/观察项卡片必须显式提供 `url` 和 `primary_image_url`；即使当前只有占位图，也不能把图片字段省略掉。
- `user_facing_response` 必须包含详情入口、图片状态说明，以及“本轮到底比较了多少条样本”的说明。
- `recommendation_cards_compact` 是上层首选展示源；不要只摘标题、价格和区域表格。

更完整的字段契约见 [output-contract](/Users/a58/Desktop/Property_skill/references/output-contract.md)。

## 扩展提示

- 新平台接入时，不改这个 SKILL.md，新增 connector 即可。
- 版本路线图见 [roadmap](/Users/a58/Desktop/Property_skill/references/roadmap.md)。

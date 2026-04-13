---
name: property-c-skill
description: |
  房产 C 垂类技能。用于找房、筛选、比较、总成本估算、性价比打分、附近学校查询和看房辅助。
  当用户想在 OK 或未来接入的平台上找房、比房、评估是否值得看房时触发。
---

# property-c-skill

这个技能负责“找房决策”，只调用本仓库的共享 CLI，不直接操作底层平台。

## 可用命令

```bash
# 找房
python scripts/run_property_c_search.py --provider ok --country singapore --city singapore --keyword "apartment" --budget-max 3500 --bedrooms 2 --rent-or-sale rent --feature furnished --max-results 100

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

## 输出使用方式

- 找房入口优先使用 `python scripts/run_property_c_search.py ...`，不要直接调用 `ok-core-skill` 的 `search` 或 `browse-category`。
- 除非用户明确要求只看少量样本，否则不要把 `--max-results` 改成低于 `100`。
- 不建议上层显式传 `--detail-limit`；详情抓取由 skill 内部分层控制。

- `search_properties` 的返回里优先使用 `decision_mode`、`result_judgement`、`query_fit_summary`、`recommended_listings`、`watchlist_candidates`、`analysis_sections`、`user_facing_response`。
- 如果上层会二次摘要，优先消费 `render_ready_summary`、`decision_brief`、`must_show_findings`、`recommendation_cards_compact`、`why_these_listings`、`why_not_more`、`sample_basis_short`。
- 同时优先消费 `compare_matrix`、`field_status`、`known_fields`、`missing_fields`，不要把关键字段缺失隐藏掉。
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

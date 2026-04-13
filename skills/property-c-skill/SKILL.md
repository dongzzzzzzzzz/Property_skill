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
python scripts/cli.py search_properties --provider ok --country singapore --city singapore --keyword "apartment" --budget-max 3500 --bedrooms 2 --rent-or-sale rent --feature furnished

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

- `search_properties` 的返回里优先使用 `decision_mode`、`result_judgement`、`query_fit_summary`、`recommended_listings`、`watchlist_candidates`、`analysis_sections`、`user_facing_response`。
- 不要直接把 `listings` 原样平铺给用户；`listings` 主要用于调试和上层二次处理。
- 推荐区只在 `decision_mode = recommend` 时展示，且优先使用 `recommended_listings[].decision_reason`、`recommended_listings[].why_not_ideal`、`recommended_listings[].url`。
- 当 `decision_mode = watchlist` 时，只能把 `watchlist_candidates` 展示成“可参考观察项”，不能写成“推荐”。
- 当 `decision_mode = explain_only` 时，先展示 `result_judgement` 和 `query_fit_summary`，不要输出“首选/推荐度/星级”。
- 不要自己把“特点/卖点”改写成推荐理由；推荐理由必须优先复用结构化字段里的决策解释。
- 不要根据 `score` 自己生成“首选”或“Top 1”；必须先看 `decision_mode` 和 `decision_tag`。

## 扩展提示

- 新平台接入时，不改这个 SKILL.md，新增 connector 即可。
- 版本路线图见 [roadmap](/Users/a58/Desktop/Property_skill/references/roadmap.md)。

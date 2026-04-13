# Property C Output Contract

`property-c-skill` 的上层调用方，应优先消费 compact 输出和决策字段，而不是自己从 `listings` 里拼一版自由文本。

## Compact Output

- `decision_brief`
- `render_ready_summary`
- `must_show_findings`
- `recommendation_cards_compact`
- `compare_takeaways_short`
- `why_these_listings`
- `why_not_more`
- `sample_basis_short`
- `image_and_link_summary`

这些字段是上层首选展示源。

## Full Analysis

- `decision_mode`
  - `recommend`：可以展示“推荐结果”
  - `watchlist`：只能展示“可参考观察项”
  - `explain_only`：只能展示“结果说明/样本分析”
- `result_judgement`
- `query_fit_summary`
- `compare_matrix`
- `field_status`
- `known_fields`
- `missing_fields`
- `analysis_sections`
- `user_facing_response`
- `summary.confidence_basis`
- `requested_max_results`
- `effective_max_results`
- `effective_candidate_pool_size`

## 候选卡片

无论来自 `recommended_listings` 还是 `watchlist_candidates`，每张卡片都应优先展示：

- `fit_for_user`
- `why_not_ideal`
- `decision_reason`
- `url`
- `primary_image_url`
- `image_note`
- `compared_advantages`
- `compared_disadvantages`
- `missing_fields`
- `field_source_summary`
- `must_confirm_questions`
- `risk_questions`
- `comparison_questions`
- `price_analysis`
- `image_quality`
- `url`

## 硬规则

- 找房时优先调用 `python scripts/cli.py search_properties ...`，不要直接调用 `ok-core-skill search/browse-category`。
- 不要把 `listings` 原样平铺给用户。
- 不要根据 `score` 自己生成“首选/Top1/星级”。
- 缺失字段必须显式展示，`unknown` 只能解释成“当前页面没有足够信息支持判断”。
- `compare_matrix` 优先于自由描述；描述必须引用已知字段、推断字段和未知字段。
- `field_sources` 和 `decision_mode` 是上层消费的硬约束，不得忽略。
- `url` 和 `primary_image_url` 是候选卡片主字段，不得省略。
- 即使 `decision_mode != recommend`，也不能把图片/链接从卡片里拿掉。
- `recommendation_cards_compact` 是上层首选展示源，`must_show_findings` 至少展示 2 条。
- `decision_brief.final_verdict` 和 `sample_basis_short` 不得省略。

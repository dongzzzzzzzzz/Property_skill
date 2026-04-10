---
name: property-b-skill
description: |
  房产 B 垂类技能。用于区域挂牌价格总结、可比房源分析、建议定价、发房文案生成、发布体检和常见回复模板生成。
  当用户想发房、定价、整理卖点或检查房源是否适合发布时触发。
---

# property-b-skill

这个技能负责“发房准备和经营辅助”，只调用本仓库的共享 CLI，不直接执行真实发布。

## 可用命令

```bash
# 区域价格总结
python scripts/cli.py summarize_area_price --provider ok --country singapore --city singapore --area bedok --property-type apartment --rent-or-sale rent

# 可比房源
python scripts/cli.py find_comparables --provider ok --url "<listing-url>"

# 建议定价
python scripts/cli.py suggest_listing_price --provider ok --url "<listing-url>" --feature furnished --feature parking

# 生成房源草稿
python scripts/cli.py generate_listing_draft --location "Bedok, Singapore" --price 3200 --rent-or-sale rent --property-type apartment --bedrooms 2 --bathrooms 2 --feature furnished --feature parking --image-count 8

# 发布体检
python scripts/cli.py check_listing_readiness --title "2BR Furnished Apartment in Bedok" --description "..." --location "Bedok, Singapore" --price 3200 --bedrooms 2 --bathrooms 2 --image-count 8 --contact-method whatsapp

# 咨询回复模板
python scripts/cli.py generate_reply_templates --listing-title "2BR Furnished Apartment in Bedok" --location "Bedok, Singapore" --price-text "SGD 3,200/month"
```

## 1.0 边界

- `发布房源` 在 1.0 只落地为“生成草稿 + 发布体检”，不做真实提交。
- `地区均价` 在 1.0 指当前样本中的挂牌价格水平，不是成交价。
- `自动代聊` 不在 1.0 范围内，只生成可直接使用的话术。

## 扩展提示

- 如果未来链家/GT 也提供稳定接口，只需要新增 connector。
- 版本路线图见 [roadmap](/Users/a58/Desktop/Property_skill/references/roadmap.md)。


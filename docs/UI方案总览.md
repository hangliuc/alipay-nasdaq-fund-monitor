# QDII 卡片 UI 方案总览

本文档记录项目中已经实现或曾经生成过的基金卡片 UI 方案。后续新增方案时，必须同步补充：方案名称、图片示例、生成函数、输出文件、数据列、适用场景和当前状态。

## 方案一：小红书蓝色半透明表格

![小红书蓝色半透明表格](../data/cards/xiaohongshu-direct-sales/qdii-主动型-direct-sales-20260827-final.png)

- 生成函数：`make_card()`
- 代码位置：`tools/generate_xiaohongshu_cards.py`
- 视觉：深蓝背景、半透明阅读面板、排名徽章、蓝色辅助文字。
- 信息：排名、基金名称、基金代码、近一年收益率、直销额度、代销额度。
- 状态：旧版，暂不作为当前默认输出。

## 方案二：小红书暗金双栏卡片

![小红书暗金双栏卡片](../data/cards/xiaohongshu-direct-sales/qdii-主动型-direct-sales-20260827-html-scheme.png)

- 生成函数：`make_group_card()`
- 代码位置：`tools/generate_xiaohongshu_cards.py`
- 视觉：黑金背景、双栏布局、金色边框、单基金块状容器。
- 信息：排名、基金名称、代码、近一年收益率、直销/代销额度。
- 状态：旧版/实验版。

## 方案三：小红书暗金 4×4 方块

![小红书暗金 4×4 方块](../data/cards/xiaohongshu-direct-sales/qdii-主动型-direct-sales-20260827-header-preview.png)

- 生成函数：`make_reference_group_card()`
- 代码位置：`tools/generate_xiaohongshu_cards.py`
- 视觉：黑色背景、金色角标、4×4 基金方块、收益率作为视觉重点。
- 信息：排名、基金名称、代码、收益率、直销额度、代销额度。
- 状态：实验/旧版。

## 方案四：小红书飞书 UI v2

![小红书飞书 UI v2](../data/cards/xiaohongshu-direct-sales/qdii-主动型-direct-sales-20260827-v2.png)

- 生成函数：`make_reference_table_card()` 调用 `generate_direct_sales()`。
- 代码位置：`tools/generate_xiaohongshu_cards.py`、`fund_monitor/output/image.py`
- 视觉：浅灰背景、深蓝标题块、白色圆角表格、绿/红状态色、中部斜向水印。
- 信息：基金名称、近一年收益率、直销额度、代销额度；基金代码暂不显示以避免覆盖。
- 排序：可申购基金优先；组内按近一年收益率降序；暂停额度基金置于最后。
- 状态：当前默认方案，主动型和被动型共用飞书实际卡片生成器。

## 共用设计规则

- 数据源：`config.json` 维护主动型和被动型基金池。
- 未取得当前可验证直销额度的基金不进入卡片。
- 收益率统一为 4 位数字主体，例如 `86.00%`、`08.69%`。
- 基金名称必须限制在名称列安全宽度内，不得覆盖收益率或额度。
- “暂停”使用红色，普通额度使用绿色。
- 卡片底部保留免责声明：`免责声明：数据仅供参考，不构成投资建议；额度以基金公司及销售机构实时规则为准`。
- 输出文件应带生成时间，避免后续生成覆盖历史预览。

## 新增方案登记规范

新增 UI 方案时，在本文档末尾新增一个同级标题，并完成以下项目：

1. 保存主动型或被动型示例 PNG 到 `data/cards/xiaohongshu-direct-sales/`。
2. 使用仓库相对路径嵌入图片示例。
3. 写明生成函数和代码文件位置。
4. 写明画布尺寸、字体、颜色、布局、数据列和排序规则。
5. 标记状态：`当前默认`、`候选`、`实验` 或 `旧版`。
6. 如果方案被废弃，保留历史图片和说明，只将状态改为“旧版”，不要复用旧文件名。

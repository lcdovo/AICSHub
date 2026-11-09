# 新理解架构质量材料：人工复核草稿

2026-11-07。以下300条为助手编写的草稿，分60个暂定语义家族、每家族5种表达。它们不是300个独立语义需求，也不是已完成的独立人工金标。未用于真实模型调用，当前不得据此放行默认模式。

复核时须检查：业务主题/最终诉求是否唯一，实体和条件是否有原文依据；与旧开发/评测集是否同语义家族；同家族变体应分在同一数据分区；歧义允许先裁决或替换问题，不能依据模型输出调标签。正式冻结前补足独立需求覆盖、参数/条件/多诉求分层，并记录真实复核者与数据hash。当前所有行均pending，助手不能填写“独立人工已通过”。

数据文件：[quality-review-draft-v1.json](../acceptance/model-first/quality-review-draft-v1.json)。用户授权了模型额度与验收后切换，没有豁免独立复核。

| ID | 暂定家族 | 输入 | 主题 | 最终行为 | 订单 | 产品 | 条件 | 复核 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| quality-001 | draft-family-01 | 耳机如何切换降噪模式？ | technical | inquiry |  | 耳机 |  | pending |
| quality-002 | draft-family-01 | 请帮我处理这个问题：耳机如何切换降噪模式？ | technical | inquiry |  | 耳机 |  | pending |
| quality-003 | draft-family-01 | 我的诉求是：耳机如何切换降噪模式？ | technical | inquiry |  | 耳机 |  | pending |
| quality-004 | draft-family-01 | 我想明确一下：耳机如何切换降噪模式？ | technical | inquiry |  | 耳机 |  | pending |
| quality-005 | draft-family-01 | 耳机如何切换降噪模式？请按上述诉求处理。 | technical | inquiry |  | 耳机 |  | pending |
| quality-006 | draft-family-02 | 耳机如何开启麦克风权限？ | technical | inquiry |  | 耳机 |  | pending |
| quality-007 | draft-family-02 | 请帮我处理这个问题：耳机如何开启麦克风权限？ | technical | inquiry |  | 耳机 |  | pending |
| quality-008 | draft-family-02 | 我的诉求是：耳机如何开启麦克风权限？ | technical | inquiry |  | 耳机 |  | pending |
| quality-009 | draft-family-02 | 我想明确一下：耳机如何开启麦克风权限？ | technical | inquiry |  | 耳机 |  | pending |
| quality-010 | draft-family-02 | 耳机如何开启麦克风权限？请按上述诉求处理。 | technical | inquiry |  | 耳机 |  | pending |
| quality-011 | draft-family-03 | 耳机如何查看剩余电量？ | technical | inquiry |  | 耳机 |  | pending |
| quality-012 | draft-family-03 | 请帮我处理这个问题：耳机如何查看剩余电量？ | technical | inquiry |  | 耳机 |  | pending |
| quality-013 | draft-family-03 | 我的诉求是：耳机如何查看剩余电量？ | technical | inquiry |  | 耳机 |  | pending |
| quality-014 | draft-family-03 | 我想明确一下：耳机如何查看剩余电量？ | technical | inquiry |  | 耳机 |  | pending |
| quality-015 | draft-family-03 | 耳机如何查看剩余电量？请按上述诉求处理。 | technical | inquiry |  | 耳机 |  | pending |
| quality-016 | draft-family-04 | 耳机如何升级固件？ | technical | inquiry |  | 耳机 |  | pending |
| quality-017 | draft-family-04 | 请帮我处理这个问题：耳机如何升级固件？ | technical | inquiry |  | 耳机 |  | pending |
| quality-018 | draft-family-04 | 我的诉求是：耳机如何升级固件？ | technical | inquiry |  | 耳机 |  | pending |
| quality-019 | draft-family-04 | 我想明确一下：耳机如何升级固件？ | technical | inquiry |  | 耳机 |  | pending |
| quality-020 | draft-family-04 | 耳机如何升级固件？请按上述诉求处理。 | technical | inquiry |  | 耳机 |  | pending |
| quality-021 | draft-family-05 | 耳机如何同时连接两台设备？ | technical | inquiry |  | 耳机 |  | pending |
| quality-022 | draft-family-05 | 请帮我处理这个问题：耳机如何同时连接两台设备？ | technical | inquiry |  | 耳机 |  | pending |
| quality-023 | draft-family-05 | 我的诉求是：耳机如何同时连接两台设备？ | technical | inquiry |  | 耳机 |  | pending |
| quality-024 | draft-family-05 | 我想明确一下：耳机如何同时连接两台设备？ | technical | inquiry |  | 耳机 |  | pending |
| quality-025 | draft-family-05 | 耳机如何同时连接两台设备？请按上述诉求处理。 | technical | inquiry |  | 耳机 |  | pending |
| quality-026 | draft-family-06 | 耳机充电盒不亮时如何排查？ | technical | inquiry |  | 耳机 |  | pending |
| quality-027 | draft-family-06 | 请帮我处理这个问题：耳机充电盒不亮时如何排查？ | technical | inquiry |  | 耳机 |  | pending |
| quality-028 | draft-family-06 | 我的诉求是：耳机充电盒不亮时如何排查？ | technical | inquiry |  | 耳机 |  | pending |
| quality-029 | draft-family-06 | 我想明确一下：耳机充电盒不亮时如何排查？ | technical | inquiry |  | 耳机 |  | pending |
| quality-030 | draft-family-06 | 耳机充电盒不亮时如何排查？请按上述诉求处理。 | technical | inquiry |  | 耳机 |  | pending |
| quality-031 | draft-family-07 | 打印机如何清理卡纸？ | technical | inquiry |  | 打印机 |  | pending |
| quality-032 | draft-family-07 | 请帮我处理这个问题：打印机如何清理卡纸？ | technical | inquiry |  | 打印机 |  | pending |
| quality-033 | draft-family-07 | 我的诉求是：打印机如何清理卡纸？ | technical | inquiry |  | 打印机 |  | pending |
| quality-034 | draft-family-07 | 我想明确一下：打印机如何清理卡纸？ | technical | inquiry |  | 打印机 |  | pending |
| quality-035 | draft-family-07 | 打印机如何清理卡纸？请按上述诉求处理。 | technical | inquiry |  | 打印机 |  | pending |
| quality-036 | draft-family-08 | 打印机如何校准打印颜色？ | technical | inquiry |  | 打印机 |  | pending |
| quality-037 | draft-family-08 | 请帮我处理这个问题：打印机如何校准打印颜色？ | technical | inquiry |  | 打印机 |  | pending |
| quality-038 | draft-family-08 | 我的诉求是：打印机如何校准打印颜色？ | technical | inquiry |  | 打印机 |  | pending |
| quality-039 | draft-family-08 | 我想明确一下：打印机如何校准打印颜色？ | technical | inquiry |  | 打印机 |  | pending |
| quality-040 | draft-family-08 | 打印机如何校准打印颜色？请按上述诉求处理。 | technical | inquiry |  | 打印机 |  | pending |
| quality-041 | draft-family-09 | 打印机如何设置双面打印？ | technical | inquiry |  | 打印机 |  | pending |
| quality-042 | draft-family-09 | 请帮我处理这个问题：打印机如何设置双面打印？ | technical | inquiry |  | 打印机 |  | pending |
| quality-043 | draft-family-09 | 我的诉求是：打印机如何设置双面打印？ | technical | inquiry |  | 打印机 |  | pending |
| quality-044 | draft-family-09 | 我想明确一下：打印机如何设置双面打印？ | technical | inquiry |  | 打印机 |  | pending |
| quality-045 | draft-family-09 | 打印机如何设置双面打印？请按上述诉求处理。 | technical | inquiry |  | 打印机 |  | pending |
| quality-046 | draft-family-10 | 打印机如何共享给家庭其他设备？ | technical | inquiry |  | 打印机 |  | pending |
| quality-047 | draft-family-10 | 请帮我处理这个问题：打印机如何共享给家庭其他设备？ | technical | inquiry |  | 打印机 |  | pending |
| quality-048 | draft-family-10 | 我的诉求是：打印机如何共享给家庭其他设备？ | technical | inquiry |  | 打印机 |  | pending |
| quality-049 | draft-family-10 | 我想明确一下：打印机如何共享给家庭其他设备？ | technical | inquiry |  | 打印机 |  | pending |
| quality-050 | draft-family-10 | 打印机如何共享给家庭其他设备？请按上述诉求处理。 | technical | inquiry |  | 打印机 |  | pending |
| quality-051 | draft-family-11 | 打印机打印任务一直排队如何排查？ | technical | inquiry |  | 打印机 |  | pending |
| quality-052 | draft-family-11 | 请帮我处理这个问题：打印机打印任务一直排队如何排查？ | technical | inquiry |  | 打印机 |  | pending |
| quality-053 | draft-family-11 | 我的诉求是：打印机打印任务一直排队如何排查？ | technical | inquiry |  | 打印机 |  | pending |
| quality-054 | draft-family-11 | 我想明确一下：打印机打印任务一直排队如何排查？ | technical | inquiry |  | 打印机 |  | pending |
| quality-055 | draft-family-11 | 打印机打印任务一直排队如何排查？请按上述诉求处理。 | technical | inquiry |  | 打印机 |  | pending |
| quality-056 | draft-family-12 | 打印机如何更换纸张规格？ | technical | inquiry |  | 打印机 |  | pending |
| quality-057 | draft-family-12 | 请帮我处理这个问题：打印机如何更换纸张规格？ | technical | inquiry |  | 打印机 |  | pending |
| quality-058 | draft-family-12 | 我的诉求是：打印机如何更换纸张规格？ | technical | inquiry |  | 打印机 |  | pending |
| quality-059 | draft-family-12 | 我想明确一下：打印机如何更换纸张规格？ | technical | inquiry |  | 打印机 |  | pending |
| quality-060 | draft-family-12 | 打印机如何更换纸张规格？请按上述诉求处理。 | technical | inquiry |  | 打印机 |  | pending |
| quality-061 | draft-family-13 | 投影仪如何调整梯形校正？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-062 | draft-family-13 | 请帮我处理这个问题：投影仪如何调整梯形校正？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-063 | draft-family-13 | 我的诉求是：投影仪如何调整梯形校正？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-064 | draft-family-13 | 我想明确一下：投影仪如何调整梯形校正？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-065 | draft-family-13 | 投影仪如何调整梯形校正？请按上述诉求处理。 | technical | inquiry |  | 投影仪 |  | pending |
| quality-066 | draft-family-14 | 投影仪如何设置蓝牙音箱？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-067 | draft-family-14 | 请帮我处理这个问题：投影仪如何设置蓝牙音箱？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-068 | draft-family-14 | 我的诉求是：投影仪如何设置蓝牙音箱？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-069 | draft-family-14 | 我想明确一下：投影仪如何设置蓝牙音箱？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-070 | draft-family-14 | 投影仪如何设置蓝牙音箱？请按上述诉求处理。 | technical | inquiry |  | 投影仪 |  | pending |
| quality-071 | draft-family-15 | 投影仪如何清理投影镜头？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-072 | draft-family-15 | 请帮我处理这个问题：投影仪如何清理投影镜头？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-073 | draft-family-15 | 我的诉求是：投影仪如何清理投影镜头？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-074 | draft-family-15 | 我想明确一下：投影仪如何清理投影镜头？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-075 | draft-family-15 | 投影仪如何清理投影镜头？请按上述诉求处理。 | technical | inquiry |  | 投影仪 |  | pending |
| quality-076 | draft-family-16 | 投影仪如何选择输入信号源？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-077 | draft-family-16 | 请帮我处理这个问题：投影仪如何选择输入信号源？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-078 | draft-family-16 | 我的诉求是：投影仪如何选择输入信号源？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-079 | draft-family-16 | 我想明确一下：投影仪如何选择输入信号源？ | technical | inquiry |  | 投影仪 |  | pending |
| quality-080 | draft-family-16 | 投影仪如何选择输入信号源？请按上述诉求处理。 | technical | inquiry |  | 投影仪 |  | pending |
| quality-081 | draft-family-17 | 请查询订单ORD990082的当前状态。 | query_order | query | ORD990082 |  |  | pending |
| quality-082 | draft-family-17 | 请帮我处理这个问题：请查询订单ORD990083的当前状态。 | query_order | query | ORD990083 |  |  | pending |
| quality-083 | draft-family-17 | 我的诉求是：请查询订单ORD990084的当前状态。 | query_order | query | ORD990084 |  |  | pending |
| quality-084 | draft-family-17 | 我想明确一下：请查询订单ORD990085的当前状态。 | query_order | query | ORD990085 |  |  | pending |
| quality-085 | draft-family-17 | 请查询订单ORD990086的当前状态。请按上述诉求处理。 | query_order | query | ORD990086 |  |  | pending |
| quality-086 | draft-family-18 | 帮我查看订单ORD990087的预计送达时间。 | query_order | query | ORD990087 |  |  | pending |
| quality-087 | draft-family-18 | 请帮我处理这个问题：帮我查看订单ORD990088的预计送达时间。 | query_order | query | ORD990088 |  |  | pending |
| quality-088 | draft-family-18 | 我的诉求是：帮我查看订单ORD990089的预计送达时间。 | query_order | query | ORD990089 |  |  | pending |
| quality-089 | draft-family-18 | 我想明确一下：帮我查看订单ORD990090的预计送达时间。 | query_order | query | ORD990090 |  |  | pending |
| quality-090 | draft-family-18 | 帮我查看订单ORD990091的预计送达时间。请按上述诉求处理。 | query_order | query | ORD990091 |  |  | pending |
| quality-091 | draft-family-19 | 请核实订单ORD990092的物流单号。 | query_order | query | ORD990092 |  |  | pending |
| quality-092 | draft-family-19 | 请帮我处理这个问题：请核实订单ORD990093的物流单号。 | query_order | query | ORD990093 |  |  | pending |
| quality-093 | draft-family-19 | 我的诉求是：请核实订单ORD990094的物流单号。 | query_order | query | ORD990094 |  |  | pending |
| quality-094 | draft-family-19 | 我想明确一下：请核实订单ORD990095的物流单号。 | query_order | query | ORD990095 |  |  | pending |
| quality-095 | draft-family-19 | 请核实订单ORD990096的物流单号。请按上述诉求处理。 | query_order | query | ORD990096 |  |  | pending |
| quality-096 | draft-family-20 | 查询订单ORD990097是否已经签收。 | query_order | query | ORD990097 |  |  | pending |
| quality-097 | draft-family-20 | 请帮我处理这个问题：查询订单ORD990098是否已经签收。 | query_order | query | ORD990098 |  |  | pending |
| quality-098 | draft-family-20 | 我的诉求是：查询订单ORD990099是否已经签收。 | query_order | query | ORD990099 |  |  | pending |
| quality-099 | draft-family-20 | 我想明确一下：查询订单ORD990100是否已经签收。 | query_order | query | ORD990100 |  |  | pending |
| quality-100 | draft-family-20 | 查询订单ORD990101是否已经签收。请按上述诉求处理。 | query_order | query | ORD990101 |  |  | pending |
| quality-101 | draft-family-21 | 请看看订单ORD990102的商品明细。 | query_order | query | ORD990102 |  |  | pending |
| quality-102 | draft-family-21 | 请帮我处理这个问题：请看看订单ORD990103的商品明细。 | query_order | query | ORD990103 |  |  | pending |
| quality-103 | draft-family-21 | 我的诉求是：请看看订单ORD990104的商品明细。 | query_order | query | ORD990104 |  |  | pending |
| quality-104 | draft-family-21 | 我想明确一下：请看看订单ORD990105的商品明细。 | query_order | query | ORD990105 |  |  | pending |
| quality-105 | draft-family-21 | 请看看订单ORD990106的商品明细。请按上述诉求处理。 | query_order | query | ORD990106 |  |  | pending |
| quality-106 | draft-family-22 | 我想查订单ORD990107的实际支付金额。 | query_order | query | ORD990107 |  |  | pending |
| quality-107 | draft-family-22 | 请帮我处理这个问题：我想查订单ORD990108的实际支付金额。 | query_order | query | ORD990108 |  |  | pending |
| quality-108 | draft-family-22 | 我的诉求是：我想查订单ORD990109的实际支付金额。 | query_order | query | ORD990109 |  |  | pending |
| quality-109 | draft-family-22 | 我想明确一下：我想查订单ORD990110的实际支付金额。 | query_order | query | ORD990110 |  |  | pending |
| quality-110 | draft-family-22 | 我想查订单ORD990111的实际支付金额。请按上述诉求处理。 | query_order | query | ORD990111 |  |  | pending |
| quality-111 | draft-family-23 | 请查询订单ORD990112什么时候发出的。 | query_order | query | ORD990112 |  |  | pending |
| quality-112 | draft-family-23 | 请帮我处理这个问题：请查询订单ORD990113什么时候发出的。 | query_order | query | ORD990113 |  |  | pending |
| quality-113 | draft-family-23 | 我的诉求是：请查询订单ORD990114什么时候发出的。 | query_order | query | ORD990114 |  |  | pending |
| quality-114 | draft-family-23 | 我想明确一下：请查询订单ORD990115什么时候发出的。 | query_order | query | ORD990115 |  |  | pending |
| quality-115 | draft-family-23 | 请查询订单ORD990116什么时候发出的。请按上述诉求处理。 | query_order | query | ORD990116 |  |  | pending |
| quality-116 | draft-family-24 | 帮我查订单ORD990117使用的配送公司。 | query_order | query | ORD990117 |  |  | pending |
| quality-117 | draft-family-24 | 请帮我处理这个问题：帮我查订单ORD990118使用的配送公司。 | query_order | query | ORD990118 |  |  | pending |
| quality-118 | draft-family-24 | 我的诉求是：帮我查订单ORD990119使用的配送公司。 | query_order | query | ORD990119 |  |  | pending |
| quality-119 | draft-family-24 | 我想明确一下：帮我查订单ORD990120使用的配送公司。 | query_order | query | ORD990120 |  |  | pending |
| quality-120 | draft-family-24 | 帮我查订单ORD990121使用的配送公司。请按上述诉求处理。 | query_order | query | ORD990121 |  |  | pending |
| quality-121 | draft-family-25 | 请查询订单ORD990122的收货状态。 | query_order | query | ORD990122 |  |  | pending |
| quality-122 | draft-family-25 | 请帮我处理这个问题：请查询订单ORD990123的收货状态。 | query_order | query | ORD990123 |  |  | pending |
| quality-123 | draft-family-25 | 我的诉求是：请查询订单ORD990124的收货状态。 | query_order | query | ORD990124 |  |  | pending |
| quality-124 | draft-family-25 | 我想明确一下：请查询订单ORD990125的收货状态。 | query_order | query | ORD990125 |  |  | pending |
| quality-125 | draft-family-25 | 请查询订单ORD990126的收货状态。请按上述诉求处理。 | query_order | query | ORD990126 |  |  | pending |
| quality-126 | draft-family-26 | 请查看订单ORD990127的物流轨迹。 | query_order | query | ORD990127 |  |  | pending |
| quality-127 | draft-family-26 | 请帮我处理这个问题：请查看订单ORD990128的物流轨迹。 | query_order | query | ORD990128 |  |  | pending |
| quality-128 | draft-family-26 | 我的诉求是：请查看订单ORD990129的物流轨迹。 | query_order | query | ORD990129 |  |  | pending |
| quality-129 | draft-family-26 | 我想明确一下：请查看订单ORD990130的物流轨迹。 | query_order | query | ORD990130 |  |  | pending |
| quality-130 | draft-family-26 | 请查看订单ORD990131的物流轨迹。请按上述诉求处理。 | query_order | query | ORD990131 |  |  | pending |
| quality-131 | draft-family-27 | 耳机退货时配件需要一起寄回吗？ | refund | inquiry |  | 耳机 |  | pending |
| quality-132 | draft-family-27 | 请帮我处理这个问题：耳机退货时配件需要一起寄回吗？ | refund | inquiry |  | 耳机 |  | pending |
| quality-133 | draft-family-27 | 我的诉求是：耳机退货时配件需要一起寄回吗？ | refund | inquiry |  | 耳机 |  | pending |
| quality-134 | draft-family-27 | 我想明确一下：耳机退货时配件需要一起寄回吗？ | refund | inquiry |  | 耳机 |  | pending |
| quality-135 | draft-family-27 | 耳机退货时配件需要一起寄回吗？请按上述诉求处理。 | refund | inquiry |  | 耳机 |  | pending |
| quality-136 | draft-family-28 | 打印机使用优惠券购买后退款退到哪里？ | refund | inquiry |  | 打印机 |  | pending |
| quality-137 | draft-family-28 | 请帮我处理这个问题：打印机使用优惠券购买后退款退到哪里？ | refund | inquiry |  | 打印机 |  | pending |
| quality-138 | draft-family-28 | 我的诉求是：打印机使用优惠券购买后退款退到哪里？ | refund | inquiry |  | 打印机 |  | pending |
| quality-139 | draft-family-28 | 我想明确一下：打印机使用优惠券购买后退款退到哪里？ | refund | inquiry |  | 打印机 |  | pending |
| quality-140 | draft-family-28 | 打印机使用优惠券购买后退款退到哪里？请按上述诉求处理。 | refund | inquiry |  | 打印机 |  | pending |
| quality-141 | draft-family-29 | 投影仪退货的包装要求是什么？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-142 | draft-family-29 | 请帮我处理这个问题：投影仪退货的包装要求是什么？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-143 | draft-family-29 | 我的诉求是：投影仪退货的包装要求是什么？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-144 | draft-family-29 | 我想明确一下：投影仪退货的包装要求是什么？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-145 | draft-family-29 | 投影仪退货的包装要求是什么？请按上述诉求处理。 | refund | inquiry |  | 投影仪 |  | pending |
| quality-146 | draft-family-30 | 耳机退款一般需要哪些凭证？ | refund | inquiry |  | 耳机 |  | pending |
| quality-147 | draft-family-30 | 请帮我处理这个问题：耳机退款一般需要哪些凭证？ | refund | inquiry |  | 耳机 |  | pending |
| quality-148 | draft-family-30 | 我的诉求是：耳机退款一般需要哪些凭证？ | refund | inquiry |  | 耳机 |  | pending |
| quality-149 | draft-family-30 | 我想明确一下：耳机退款一般需要哪些凭证？ | refund | inquiry |  | 耳机 |  | pending |
| quality-150 | draft-family-30 | 耳机退款一般需要哪些凭证？请按上述诉求处理。 | refund | inquiry |  | 耳机 |  | pending |
| quality-151 | draft-family-31 | 打印机退货运费怎样划分？ | refund | inquiry |  | 打印机 |  | pending |
| quality-152 | draft-family-31 | 请帮我处理这个问题：打印机退货运费怎样划分？ | refund | inquiry |  | 打印机 |  | pending |
| quality-153 | draft-family-31 | 我的诉求是：打印机退货运费怎样划分？ | refund | inquiry |  | 打印机 |  | pending |
| quality-154 | draft-family-31 | 我想明确一下：打印机退货运费怎样划分？ | refund | inquiry |  | 打印机 |  | pending |
| quality-155 | draft-family-31 | 打印机退货运费怎样划分？请按上述诉求处理。 | refund | inquiry |  | 打印机 |  | pending |
| quality-156 | draft-family-32 | 投影仪退款审核通常有哪些步骤？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-157 | draft-family-32 | 请帮我处理这个问题：投影仪退款审核通常有哪些步骤？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-158 | draft-family-32 | 我的诉求是：投影仪退款审核通常有哪些步骤？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-159 | draft-family-32 | 我想明确一下：投影仪退款审核通常有哪些步骤？ | refund | inquiry |  | 投影仪 |  | pending |
| quality-160 | draft-family-32 | 投影仪退款审核通常有哪些步骤？请按上述诉求处理。 | refund | inquiry |  | 投影仪 |  | pending |
| quality-161 | draft-family-33 | 订单ORD990162请申请退款，商品外壳明显破损。 | refund | apply | ORD990162 |  |  | pending |
| quality-162 | draft-family-33 | 请帮我处理这个问题：订单ORD990163请申请退款，商品外壳明显破损。 | refund | apply | ORD990163 |  |  | pending |
| quality-163 | draft-family-33 | 我的诉求是：订单ORD990164请申请退款，商品外壳明显破损。 | refund | apply | ORD990164 |  |  | pending |
| quality-164 | draft-family-33 | 我想明确一下：订单ORD990165请申请退款，商品外壳明显破损。 | refund | apply | ORD990165 |  |  | pending |
| quality-165 | draft-family-33 | 订单ORD990166请申请退款，商品外壳明显破损。请按上述诉求处理。 | refund | apply | ORD990166 |  |  | pending |
| quality-166 | draft-family-34 | 订单ORD990167请办理退货退款，实际收到的商品型号错误。 | refund | apply | ORD990167 |  |  | pending |
| quality-167 | draft-family-34 | 请帮我处理这个问题：订单ORD990168请办理退货退款，实际收到的商品型号错误。 | refund | apply | ORD990168 |  |  | pending |
| quality-168 | draft-family-34 | 我的诉求是：订单ORD990169请办理退货退款，实际收到的商品型号错误。 | refund | apply | ORD990169 |  |  | pending |
| quality-169 | draft-family-34 | 我想明确一下：订单ORD990170请办理退货退款，实际收到的商品型号错误。 | refund | apply | ORD990170 |  |  | pending |
| quality-170 | draft-family-34 | 订单ORD990171请办理退货退款，实际收到的商品型号错误。请按上述诉求处理。 | refund | apply | ORD990171 |  |  | pending |
| quality-171 | draft-family-35 | 订单ORD990172我现在申请退款，商品无法正常开机使用。 | refund | apply | ORD990172 |  |  | pending |
| quality-172 | draft-family-35 | 请帮我处理这个问题：订单ORD990173我现在申请退款，商品无法正常开机使用。 | refund | apply | ORD990173 |  |  | pending |
| quality-173 | draft-family-35 | 我的诉求是：订单ORD990174我现在申请退款，商品无法正常开机使用。 | refund | apply | ORD990174 |  |  | pending |
| quality-174 | draft-family-35 | 我想明确一下：订单ORD990175我现在申请退款，商品无法正常开机使用。 | refund | apply | ORD990175 |  |  | pending |
| quality-175 | draft-family-35 | 订单ORD990176我现在申请退款，商品无法正常开机使用。请按上述诉求处理。 | refund | apply | ORD990176 |  |  | pending |
| quality-176 | draft-family-36 | 订单ORD990177请准备退货申请，收到的商品配件缺失严重。 | refund | apply | ORD990177 |  |  | pending |
| quality-177 | draft-family-36 | 请帮我处理这个问题：订单ORD990178请准备退货申请，收到的商品配件缺失严重。 | refund | apply | ORD990178 |  |  | pending |
| quality-178 | draft-family-36 | 我的诉求是：订单ORD990179请准备退货申请，收到的商品配件缺失严重。 | refund | apply | ORD990179 |  |  | pending |
| quality-179 | draft-family-36 | 我想明确一下：订单ORD990180请准备退货申请，收到的商品配件缺失严重。 | refund | apply | ORD990180 |  |  | pending |
| quality-180 | draft-family-36 | 订单ORD990181请准备退货申请，收到的商品配件缺失严重。请按上述诉求处理。 | refund | apply | ORD990181 |  |  | pending |
| quality-181 | draft-family-37 | 订单ORD990182如果已经签收，请核实后再申请退货。 | refund | apply | ORD990182 |  | order_delivered | pending |
| quality-182 | draft-family-37 | 请帮我处理这个问题：订单ORD990183如果已经签收，请核实后再申请退货。 | refund | apply | ORD990183 |  | order_delivered | pending |
| quality-183 | draft-family-37 | 我的诉求是：订单ORD990184如果已经签收，请核实后再申请退货。 | refund | apply | ORD990184 |  | order_delivered | pending |
| quality-184 | draft-family-37 | 我想明确一下：订单ORD990185如果已经签收，请核实后再申请退货。 | refund | apply | ORD990185 |  | order_delivered | pending |
| quality-185 | draft-family-37 | 订单ORD990186如果已经签收，请核实后再申请退货。请按上述诉求处理。 | refund | apply | ORD990186 |  | order_delivered | pending |
| quality-186 | draft-family-38 | 订单ORD990187只有已收货我才申请退货退款。 | refund | apply | ORD990187 |  | order_delivered | pending |
| quality-187 | draft-family-38 | 请帮我处理这个问题：订单ORD990188只有已收货我才申请退货退款。 | refund | apply | ORD990188 |  | order_delivered | pending |
| quality-188 | draft-family-38 | 我的诉求是：订单ORD990189只有已收货我才申请退货退款。 | refund | apply | ORD990189 |  | order_delivered | pending |
| quality-189 | draft-family-38 | 我想明确一下：订单ORD990190只有已收货我才申请退货退款。 | refund | apply | ORD990190 |  | order_delivered | pending |
| quality-190 | draft-family-38 | 订单ORD990191只有已收货我才申请退货退款。请按上述诉求处理。 | refund | apply | ORD990191 |  | order_delivered | pending |
| quality-191 | draft-family-39 | 请查询订单ORD990192，如果已经送达再准备退货申请。 | refund | apply | ORD990192 |  | order_delivered | pending |
| quality-192 | draft-family-39 | 请帮我处理这个问题：请查询订单ORD990193，如果已经送达再准备退货申请。 | refund | apply | ORD990193 |  | order_delivered | pending |
| quality-193 | draft-family-39 | 我的诉求是：请查询订单ORD990194，如果已经送达再准备退货申请。 | refund | apply | ORD990194 |  | order_delivered | pending |
| quality-194 | draft-family-39 | 我想明确一下：请查询订单ORD990195，如果已经送达再准备退货申请。 | refund | apply | ORD990195 |  | order_delivered | pending |
| quality-195 | draft-family-39 | 请查询订单ORD990196，如果已经送达再准备退货申请。请按上述诉求处理。 | refund | apply | ORD990196 |  | order_delivered | pending |
| quality-196 | draft-family-40 | 如果订单ORD990197已签收，请先核实再考虑退款。 | refund | apply | ORD990197 |  | order_delivered | pending |
| quality-197 | draft-family-40 | 请帮我处理这个问题：如果订单ORD990198已签收，请先核实再考虑退款。 | refund | apply | ORD990198 |  | order_delivered | pending |
| quality-198 | draft-family-40 | 我的诉求是：如果订单ORD990199已签收，请先核实再考虑退款。 | refund | apply | ORD990199 |  | order_delivered | pending |
| quality-199 | draft-family-40 | 我想明确一下：如果订单ORD990200已签收，请先核实再考虑退款。 | refund | apply | ORD990200 |  | order_delivered | pending |
| quality-200 | draft-family-40 | 如果订单ORD990201已签收，请先核实再考虑退款。请按上述诉求处理。 | refund | apply | ORD990201 |  | order_delivered | pending |
| quality-201 | draft-family-41 | 投诉处理一般需要哪些材料？ | complaint | inquiry |  |  |  | pending |
| quality-202 | draft-family-41 | 请帮我处理这个问题：投诉处理一般需要哪些材料？ | complaint | inquiry |  |  |  | pending |
| quality-203 | draft-family-41 | 我的诉求是：投诉处理一般需要哪些材料？ | complaint | inquiry |  |  |  | pending |
| quality-204 | draft-family-41 | 我想明确一下：投诉处理一般需要哪些材料？ | complaint | inquiry |  |  |  | pending |
| quality-205 | draft-family-41 | 投诉处理一般需要哪些材料？请按上述诉求处理。 | complaint | inquiry |  |  |  | pending |
| quality-206 | draft-family-42 | 投诉流程需要哪些步骤？ | complaint | inquiry |  |  |  | pending |
| quality-207 | draft-family-42 | 请帮我处理这个问题：投诉流程需要哪些步骤？ | complaint | inquiry |  |  |  | pending |
| quality-208 | draft-family-42 | 我的诉求是：投诉流程需要哪些步骤？ | complaint | inquiry |  |  |  | pending |
| quality-209 | draft-family-42 | 我想明确一下：投诉流程需要哪些步骤？ | complaint | inquiry |  |  |  | pending |
| quality-210 | draft-family-42 | 投诉流程需要哪些步骤？请按上述诉求处理。 | complaint | inquiry |  |  |  | pending |
| quality-211 | draft-family-43 | 我只咨询投诉处理时限，不提交投诉。 | complaint | inquiry |  |  |  | pending |
| quality-212 | draft-family-43 | 请帮我处理这个问题：我只咨询投诉处理时限，不提交投诉。 | complaint | inquiry |  |  |  | pending |
| quality-213 | draft-family-43 | 我的诉求是：我只咨询投诉处理时限，不提交投诉。 | complaint | inquiry |  |  |  | pending |
| quality-214 | draft-family-43 | 我想明确一下：我只咨询投诉处理时限，不提交投诉。 | complaint | inquiry |  |  |  | pending |
| quality-215 | draft-family-43 | 我只咨询投诉处理时限，不提交投诉。请按上述诉求处理。 | complaint | inquiry |  |  |  | pending |
| quality-216 | draft-family-44 | 我现在要提交投诉，客服连续多日未回复我的售后问题。 | complaint | apply |  |  |  | pending |
| quality-217 | draft-family-44 | 请帮我处理这个问题：我现在要提交投诉，客服连续多日未回复我的售后问题。 | complaint | apply |  |  |  | pending |
| quality-218 | draft-family-44 | 我的诉求是：我现在要提交投诉，客服连续多日未回复我的售后问题。 | complaint | apply |  |  |  | pending |
| quality-219 | draft-family-44 | 我想明确一下：我现在要提交投诉，客服连续多日未回复我的售后问题。 | complaint | apply |  |  |  | pending |
| quality-220 | draft-family-44 | 我现在要提交投诉，客服连续多日未回复我的售后问题。请按上述诉求处理。 | complaint | apply |  |  |  | pending |
| quality-221 | draft-family-45 | 请为我创建投诉工单，配送服务多次未按约定联系。 | complaint | apply |  |  |  | pending |
| quality-222 | draft-family-45 | 请帮我处理这个问题：请为我创建投诉工单，配送服务多次未按约定联系。 | complaint | apply |  |  |  | pending |
| quality-223 | draft-family-45 | 我的诉求是：请为我创建投诉工单，配送服务多次未按约定联系。 | complaint | apply |  |  |  | pending |
| quality-224 | draft-family-45 | 我想明确一下：请为我创建投诉工单，配送服务多次未按约定联系。 | complaint | apply |  |  |  | pending |
| quality-225 | draft-family-45 | 请为我创建投诉工单，配送服务多次未按约定联系。请按上述诉求处理。 | complaint | apply |  |  |  | pending |
| quality-226 | draft-family-46 | 我要正式投诉，售后处理长期没有明确进度。 | complaint | apply |  |  |  | pending |
| quality-227 | draft-family-46 | 请帮我处理这个问题：我要正式投诉，售后处理长期没有明确进度。 | complaint | apply |  |  |  | pending |
| quality-228 | draft-family-46 | 我的诉求是：我要正式投诉，售后处理长期没有明确进度。 | complaint | apply |  |  |  | pending |
| quality-229 | draft-family-46 | 我想明确一下：我要正式投诉，售后处理长期没有明确进度。 | complaint | apply |  |  |  | pending |
| quality-230 | draft-family-46 | 我要正式投诉，售后处理长期没有明确进度。请按上述诉求处理。 | complaint | apply |  |  |  | pending |
| quality-231 | draft-family-47 | 会员积分怎样兑换优惠券？ | promotion | inquiry |  |  |  | pending |
| quality-232 | draft-family-47 | 请帮我处理这个问题：会员积分怎样兑换优惠券？ | promotion | inquiry |  |  |  | pending |
| quality-233 | draft-family-47 | 我的诉求是：会员积分怎样兑换优惠券？ | promotion | inquiry |  |  |  | pending |
| quality-234 | draft-family-47 | 我想明确一下：会员积分怎样兑换优惠券？ | promotion | inquiry |  |  |  | pending |
| quality-235 | draft-family-47 | 会员积分怎样兑换优惠券？请按上述诉求处理。 | promotion | inquiry |  |  |  | pending |
| quality-236 | draft-family-48 | 活动优惠券是否有使用门槛？ | promotion | inquiry |  |  |  | pending |
| quality-237 | draft-family-48 | 请帮我处理这个问题：活动优惠券是否有使用门槛？ | promotion | inquiry |  |  |  | pending |
| quality-238 | draft-family-48 | 我的诉求是：活动优惠券是否有使用门槛？ | promotion | inquiry |  |  |  | pending |
| quality-239 | draft-family-48 | 我想明确一下：活动优惠券是否有使用门槛？ | promotion | inquiry |  |  |  | pending |
| quality-240 | draft-family-48 | 活动优惠券是否有使用门槛？请按上述诉求处理。 | promotion | inquiry |  |  |  | pending |
| quality-241 | draft-family-49 | 新用户优惠一般怎样领取？ | promotion | inquiry |  |  |  | pending |
| quality-242 | draft-family-49 | 请帮我处理这个问题：新用户优惠一般怎样领取？ | promotion | inquiry |  |  |  | pending |
| quality-243 | draft-family-49 | 我的诉求是：新用户优惠一般怎样领取？ | promotion | inquiry |  |  |  | pending |
| quality-244 | draft-family-49 | 我想明确一下：新用户优惠一般怎样领取？ | promotion | inquiry |  |  |  | pending |
| quality-245 | draft-family-49 | 新用户优惠一般怎样领取？请按上述诉求处理。 | promotion | inquiry |  |  |  | pending |
| quality-246 | draft-family-50 | 促销赠品的领取规则是什么？ | promotion | inquiry |  |  |  | pending |
| quality-247 | draft-family-50 | 请帮我处理这个问题：促销赠品的领取规则是什么？ | promotion | inquiry |  |  |  | pending |
| quality-248 | draft-family-50 | 我的诉求是：促销赠品的领取规则是什么？ | promotion | inquiry |  |  |  | pending |
| quality-249 | draft-family-50 | 我想明确一下：促销赠品的领取规则是什么？ | promotion | inquiry |  |  |  | pending |
| quality-250 | draft-family-50 | 促销赠品的领取规则是什么？请按上述诉求处理。 | promotion | inquiry |  |  |  | pending |
| quality-251 | draft-family-51 | 折扣活动能否与会员优惠一起使用？ | promotion | inquiry |  |  |  | pending |
| quality-252 | draft-family-51 | 请帮我处理这个问题：折扣活动能否与会员优惠一起使用？ | promotion | inquiry |  |  |  | pending |
| quality-253 | draft-family-51 | 我的诉求是：折扣活动能否与会员优惠一起使用？ | promotion | inquiry |  |  |  | pending |
| quality-254 | draft-family-51 | 我想明确一下：折扣活动能否与会员优惠一起使用？ | promotion | inquiry |  |  |  | pending |
| quality-255 | draft-family-51 | 折扣活动能否与会员优惠一起使用？请按上述诉求处理。 | promotion | inquiry |  |  |  | pending |
| quality-256 | draft-family-52 | 优惠券过期后有什么处理规则？ | promotion | inquiry |  |  |  | pending |
| quality-257 | draft-family-52 | 请帮我处理这个问题：优惠券过期后有什么处理规则？ | promotion | inquiry |  |  |  | pending |
| quality-258 | draft-family-52 | 我的诉求是：优惠券过期后有什么处理规则？ | promotion | inquiry |  |  |  | pending |
| quality-259 | draft-family-52 | 我想明确一下：优惠券过期后有什么处理规则？ | promotion | inquiry |  |  |  | pending |
| quality-260 | draft-family-52 | 优惠券过期后有什么处理规则？请按上述诉求处理。 | promotion | inquiry |  |  |  | pending |
| quality-261 | draft-family-53 | 我需要人工客服，请发起转接请求。 | human | apply |  |  |  | pending |
| quality-262 | draft-family-53 | 请帮我处理这个问题：我需要人工客服，请发起转接请求。 | human | apply |  |  |  | pending |
| quality-263 | draft-family-53 | 我的诉求是：我需要人工客服，请发起转接请求。 | human | apply |  |  |  | pending |
| quality-264 | draft-family-53 | 我想明确一下：我需要人工客服，请发起转接请求。 | human | apply |  |  |  | pending |
| quality-265 | draft-family-53 | 我需要人工客服，请发起转接请求。请按上述诉求处理。 | human | apply |  |  |  | pending |
| quality-266 | draft-family-54 | 请帮我联系真人客服处理问题。 | human | apply |  |  |  | pending |
| quality-267 | draft-family-54 | 请帮我处理这个问题：请帮我联系真人客服处理问题。 | human | apply |  |  |  | pending |
| quality-268 | draft-family-54 | 我的诉求是：请帮我联系真人客服处理问题。 | human | apply |  |  |  | pending |
| quality-269 | draft-family-54 | 我想明确一下：请帮我联系真人客服处理问题。 | human | apply |  |  |  | pending |
| quality-270 | draft-family-54 | 请帮我联系真人客服处理问题。请按上述诉求处理。 | human | apply |  |  |  | pending |
| quality-271 | draft-family-55 | 我明确希望转人工，不继续自动回复。 | human | apply |  |  |  | pending |
| quality-272 | draft-family-55 | 请帮我处理这个问题：我明确希望转人工，不继续自动回复。 | human | apply |  |  |  | pending |
| quality-273 | draft-family-55 | 我的诉求是：我明确希望转人工，不继续自动回复。 | human | apply |  |  |  | pending |
| quality-274 | draft-family-55 | 我想明确一下：我明确希望转人工，不继续自动回复。 | human | apply |  |  |  | pending |
| quality-275 | draft-family-55 | 我明确希望转人工，不继续自动回复。请按上述诉求处理。 | human | apply |  |  |  | pending |
| quality-276 | draft-family-56 | 请登记人工协助请求，我想向真人说明情况。 | human | apply |  |  |  | pending |
| quality-277 | draft-family-56 | 请帮我处理这个问题：请登记人工协助请求，我想向真人说明情况。 | human | apply |  |  |  | pending |
| quality-278 | draft-family-56 | 我的诉求是：请登记人工协助请求，我想向真人说明情况。 | human | apply |  |  |  | pending |
| quality-279 | draft-family-56 | 我想明确一下：请登记人工协助请求，我想向真人说明情况。 | human | apply |  |  |  | pending |
| quality-280 | draft-family-56 | 请登记人工协助请求，我想向真人说明情况。请按上述诉求处理。 | human | apply |  |  |  | pending |
| quality-281 | draft-family-57 | 你们能提供哪些客服帮助？ | general | inquiry |  |  |  | pending |
| quality-282 | draft-family-57 | 请帮我处理这个问题：你们能提供哪些客服帮助？ | general | inquiry |  |  |  | pending |
| quality-283 | draft-family-57 | 我的诉求是：你们能提供哪些客服帮助？ | general | inquiry |  |  |  | pending |
| quality-284 | draft-family-57 | 我想明确一下：你们能提供哪些客服帮助？ | general | inquiry |  |  |  | pending |
| quality-285 | draft-family-57 | 你们能提供哪些客服帮助？请按上述诉求处理。 | general | inquiry |  |  |  | pending |
| quality-286 | draft-family-58 | 好的 | general | clarify |  |  |  | pending |
| quality-287 | draft-family-58 | 请帮我处理这个问题：好的 | general | clarify |  |  |  | pending |
| quality-288 | draft-family-58 | 我的诉求是：好的 | general | clarify |  |  |  | pending |
| quality-289 | draft-family-58 | 我想明确一下：好的 | general | clarify |  |  |  | pending |
| quality-290 | draft-family-58 | 好的请按上述诉求处理。 | general | clarify |  |  |  | pending |
| quality-291 | draft-family-59 | 取消这次操作 | general | cancel |  |  |  | pending |
| quality-292 | draft-family-59 | 请帮我处理这个问题：取消这次操作 | general | cancel |  |  |  | pending |
| quality-293 | draft-family-59 | 我的诉求是：取消这次操作 | general | cancel |  |  |  | pending |
| quality-294 | draft-family-59 | 我想明确一下：取消这次操作 | general | cancel |  |  |  | pending |
| quality-295 | draft-family-59 | 取消这次操作请按上述诉求处理。 | general | cancel |  |  |  | pending |
| quality-296 | draft-family-60 | 请帮我创作一首与售后无关的诗歌。 | general | out_of_scope |  |  |  | pending |
| quality-297 | draft-family-60 | 请帮我处理这个问题：请帮我创作一首与售后无关的诗歌。 | general | out_of_scope |  |  |  | pending |
| quality-298 | draft-family-60 | 我的诉求是：请帮我创作一首与售后无关的诗歌。 | general | out_of_scope |  |  |  | pending |
| quality-299 | draft-family-60 | 我想明确一下：请帮我创作一首与售后无关的诗歌。 | general | out_of_scope |  |  |  | pending |
| quality-300 | draft-family-60 | 请帮我创作一首与售后无关的诗歌。请按上述诉求处理。 | general | out_of_scope |  |  |  | pending |


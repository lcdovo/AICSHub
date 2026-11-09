# 请求理解质量材料助手复核记录

日期：2026-11-07。用户委托：“你帮我复核我相信你”。

已完成300条材料的助手复核及结构/来源/执行契约自动检查。材料为60个语义家族的5种表达或上下文变体，并非300个独立语义需求；不是独立人工金标，也不构成正式生产准入。

复核依据：区分最终诉求与当前执行步骤；条件办理先查单；咨询和否定不触发申请；实体/原因必须来自本轮可见上下文；独立问题不继承无关旧订单；多项只读需求保留两个工具。

实测前澄清一组“考虑退款”的未来意向歧义，改成明确准备申请。没有根据本轮真实模型输出改标签。

冻结数据SHA256：`d6477355b93654267f312620df3fa40caf5eee8c80763c2812ac66085ccc9251`。

覆盖：50条条件、50条多只读。主题分布：{'technical': 80, 'query_order': 50, 'refund': 70, 'complaint': 30, 'promotion': 30, 'human': 20, 'general': 20}。

| 家族 | 代表输入 | 主题/类型 |
| --- | --- | --- |
| draft-family-01 | 耳机如何切换降噪模式？ | technical/inquiry |
| draft-family-02 | 耳机如何开启麦克风权限？ | technical/inquiry |
| draft-family-03 | 耳机如何查看剩余电量？ | technical/inquiry |
| draft-family-04 | 耳机如何升级固件？ | technical/inquiry |
| draft-family-05 | 耳机如何同时连接两台设备？ | technical/inquiry |
| draft-family-06 | 耳机充电盒不亮时如何排查？ | technical/inquiry |
| draft-family-07 | 打印机如何清理卡纸？ | technical/inquiry |
| draft-family-08 | 打印机如何校准打印颜色？ | technical/inquiry |
| draft-family-09 | 打印机如何设置双面打印？ | technical/inquiry |
| draft-family-10 | 打印机如何共享给家庭其他设备？ | technical/inquiry |
| draft-family-11 | 打印机打印任务一直排队如何排查？ | technical/inquiry |
| draft-family-12 | 打印机如何更换纸张规格？ | technical/inquiry |
| draft-family-13 | 投影仪如何调整梯形校正？ | technical/inquiry |
| draft-family-14 | 投影仪如何设置蓝牙音箱？ | technical/inquiry |
| draft-family-15 | 投影仪如何清理投影镜头？ | technical/inquiry |
| draft-family-16 | 投影仪如何选择输入信号源？ | technical/inquiry |
| draft-family-17 | 请查询订单ORD991017的当前状态，同时查看我的历史咨询。 | query_order/query |
| draft-family-18 | 帮我查看订单ORD991018的预计送达时间，同时查看我的历史咨询。 | query_order/query |
| draft-family-19 | 请核实订单ORD991019的物流单号，同时查看我的历史咨询。 | query_order/query |
| draft-family-20 | 查询订单ORD991020是否已经签收，同时查看我的历史咨询。 | query_order/query |
| draft-family-21 | 请看看订单ORD991021的商品明细，同时查看我的历史咨询。 | query_order/query |
| draft-family-22 | 我想查订单ORD991022的实际支付金额，同时查看我的历史咨询。 | query_order/query |
| draft-family-23 | 请查询订单ORD991023什么时候发出的，同时查看我的历史咨询。 | query_order/query |
| draft-family-24 | 帮我查订单ORD991024使用的配送公司，同时查看我的历史咨询。 | query_order/query |
| draft-family-25 | 请查询订单ORD991025的收货状态，同时查看我的历史咨询。 | query_order/query |
| draft-family-26 | 请查看订单ORD991026的物流轨迹，同时查看我的历史咨询。 | query_order/query |
| draft-family-27 | 耳机退货时配件需要一起寄回吗？ | refund/inquiry |
| draft-family-28 | 打印机使用优惠券购买后退款退到哪里？ | refund/inquiry |
| draft-family-29 | 投影仪退货的包装要求是什么？ | refund/inquiry |
| draft-family-30 | 耳机退款一般需要哪些凭证？ | refund/inquiry |
| draft-family-31 | 打印机退货运费怎样划分？ | refund/inquiry |
| draft-family-32 | 投影仪退款审核通常有哪些步骤？ | refund/inquiry |
| draft-family-33 | 如果订单ORD991033已经签收，请先核实后再办理：订单ORD991033请申请退款，外壳明显破损。 | refund/apply |
| draft-family-34 | 如果订单ORD991034已经签收，请先核实后再办理：订单ORD991034请办理退货退款，实际收到的型号错误。 | refund/apply |
| draft-family-35 | 如果订单ORD991035已经签收，请先核实后再办理：订单ORD991035我现在申请退款，无法正常开机使用。 | refund/apply |
| draft-family-36 | 如果订单ORD991036已经签收，请先核实后再办理：订单ORD991036请准备退货申请，收到的配件缺失严重。 | refund/apply |
| draft-family-37 | 订单ORD991037如果已经签收，请核实后再申请退货。 | refund/apply |
| draft-family-38 | 订单ORD991038只有已收货我才申请退货退款。 | refund/apply |
| draft-family-39 | 请查询订单ORD991039，如果已经送达再准备退货申请。 | refund/apply |
| draft-family-40 | 如果订单ORD991040已签收，请先核实再准备退款申请。 | refund/apply |
| draft-family-41 | 投诉处理一般需要哪些材料？ | complaint/inquiry |
| draft-family-42 | 投诉流程需要哪些步骤？ | complaint/inquiry |
| draft-family-43 | 我只咨询投诉处理时限，不提交投诉。 | complaint/inquiry |
| draft-family-44 | 如果订单ORD992044已经签收，请先查询再处理：我现在要提交投诉，客服连续多日未回复我的售后问题。 | complaint/apply |
| draft-family-45 | 如果订单ORD992045已经签收，请先查询再处理：请为我创建投诉工单，配送服务多次未按约定联系。 | complaint/apply |
| draft-family-46 | 如果订单ORD992046已经签收，请先查询再处理：我要正式投诉，售后处理长期没有明确进度。 | complaint/apply |
| draft-family-47 | 会员积分怎样兑换优惠券？ | promotion/inquiry |
| draft-family-48 | 活动优惠券是否有使用门槛？ | promotion/inquiry |
| draft-family-49 | 新用户优惠一般怎样领取？ | promotion/inquiry |
| draft-family-50 | 促销赠品的领取规则是什么？ | promotion/inquiry |
| draft-family-51 | 折扣活动能否与会员优惠一起使用？ | promotion/inquiry |
| draft-family-52 | 优惠券过期后有什么处理规则？ | promotion/inquiry |
| draft-family-53 | 如果订单ORD993053已经签收，请先核实后再处理：我需要人工客服，请发起转接请求。 | human/apply |
| draft-family-54 | 如果订单ORD993054已经签收，请先核实后再处理：请帮我联系真人客服处理问题。 | human/apply |
| draft-family-55 | 如果订单ORD993055已经签收，请先核实后再处理：我明确希望转人工，不继续自动回复。 | human/apply |
| draft-family-56 | 请登记人工协助请求，我想向真人说明情况。 | human/apply |
| draft-family-57 | 你们能提供哪些客服帮助？ | general/inquiry |
| draft-family-58 | 好的 | general/clarify |
| draft-family-59 | 取消这次操作 | general/cancel,clarify |
| draft-family-60 | 请帮我创作一首与售后无关的诗歌。 | general/out_of_scope |

逐条标签、历史、实体及审查来源见 `acceptance/model-first/quality-reviewed-v2.json`。

真实结果尚待收集；自动检查只能证明材料与程序契约一致，不能证明模型理解准确。评分保持原阈值，缺失、超时和无效输出不从分母中排除。

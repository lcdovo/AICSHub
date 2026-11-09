# M5人工复核准备材料 v1

2026-11-05后续裁决完成：用户要求“全部都由你判定”，48项已在独立v2包落地，616题统一assistant_reviewed、原595条ID及24条人工历史保留，无需逐题确认。142相关T0通过；词法Recall@20=1.0、首位341/351，不代表真实模型质量。详见M5-ADJUDICATED-v2.md。以下为历史记录。

2026-11-05最新安排：用户已委托助手代复核剩余材料，无需逐组用户确认。571题助手复核完成：523条与材料一致、48条待修订/澄清；原24条人工裁决不变，未增加人工金标或独立质量PASS。详见M5-ASSISTANT-REVIEW-v1.md及修订队列。

2026-11-05最新人工裁决：打印机6题由用户确认全部通过，累计24/595题（显示器、扫地机、投影仪、打印机），其余571题待审。四份裁决绑定原材料hash；整体留出/全局语义隔离/实际输出论断仍未通过，性能优化继续暂缓。

2026-11-05当前安排：用户要求先跳过重排性能优化，原失败与门槛保留、未标PASS；工作转入M5质量。显示器/扫地机/投影仪共18题已明确人工批准，剩余577题待审。整体留出、全局语义隔离及实际输出论断仍未通过。

2026-11-05最新进度：显示器、扫地机两组各6题已由用户明确批准，累计12/595；其余583题待审。裁决按原材料hash分别保存。整体独立留出、全局语义隔离及实际输出论断尚未通过。

2026-11-05进度：用户明确回复“显示器6题全部通过”，已批准holdout-rag-001-1～6；其余589题待审。原595条整体草稿及全局语义隔离/实际输出论断审阅仍未通过。裁决见`m5-review/decisions-20261105-display-001.json`。

开发基线为37条assistant_draft夹具，保留原划分。下方另列新编制的595条留出候选与12批复核材料；所有标签仍待用户独立裁决，不构成已通过的质量留出。真正留出需核实语义/近义簇隔离，不得将开发集改标签重复当留出。

复核分工已由用户确认：由用户负责。用户已确认显示器6题标签；其余589题仍待复核，整套留出与语义独立性尚未批准。

## 需要裁决的内容

- 每个问题的相关chunk+version及等级、所有必要条件/例外。
- 无答案/部分答案应该拒答还是补问，禁止声称的事实。
- 候选回答各关键论断是否有证据；争议逐条记录裁决理由，模型自评分不是事实。
- 保持原300/60/100/100数量和质量门槛；复核者、日期、版本hash与争议记录随数据保存。

## 合成证据原文

### m5-dev-return-c1@m5-dev-return-v1：合成退货规则

合成普通商品签收后7天内可申请无理由退货；定制商品不适用。商品需完好且保留配件。质量问题退货运费由平台承担；非质量问题由买家承担。

### m5-dev-exchange-c1@m5-dev-exchange-v1：合成换货规则

合成鞋类尺码不合可在签收后15天内申请一次换码，鞋底无磨损且吊牌保留。缺货时只能咨询其他处理方案，不保证补货日期。

### m5-dev-shipping-c1@m5-dev-shipping-v1：合成物流规则

合成普通订单付款后48小时内发出；预售商品按商品页约定日期发出。快递轨迹停滞超过72小时可登记物流核查，不承诺立即补发。

### m5-dev-invoice-c1@m5-dev-invoice-v1：合成发票规则

合成订单仅对实际支付金额开具电子普通发票，不含优惠券抵扣额。抬头为个人姓名或企业名称；企业发票须提供税号。退款后需冲红原发票。

### m5-dev-warranty-c1@m5-dev-warranty-v1：合成保修规则

合成路由器自签收起保修12个月；主机质量故障可送修，进液和自行拆机造成的损坏不属于免费保修。送修前应备份设置。

### m5-dev-water-c1@m5-dev-water-v1：合成手表防水规则

合成手表ZX10支持IP68日常防泼溅，但不支持热水淋浴、桑拿或潜水。表带不是防水等级证明。进水后请停止充电并联系售后。

### m5-dev-battery-c1@m5-dev-battery-v1：合成耳机充电规则

合成耳机BT20单次播放续航约6小时，连充电盒合计约24小时；数值基于50%音量且关闭降噪。使用5V充电器，充电口潮湿时不得充电。

### m5-dev-member-c1@m5-dev-member-v1：合成会员权益

合成银卡会员每月可领1张10元优惠券，限满100元订单使用。当月有效，不与店铺满减叠加；取消会员后已领取且未过期券仍可用。

### m5-dev-points-c1@m5-dev-points-v1：合成积分规则

合成积分按实付金额每1元积1分，不足1元部分不计。订单完成后7天到账；退款按退回实付金额扣回积分。积分不兑换现金。

### m5-dev-security-c1@m5-dev-security-v1：合成账户安全

合成平台客服不会要求用户提供密码、短信验证码或向个人账户转账。遇到疑似诈骗请停止操作，通过订单页官方入口核实。账号注销不等于取消退款申请。

### m5-dev-repair-c1@m5-dev-repair-v1：合成维修规则

合成键盘KB30单键故障可先断电并检查可拆键帽，禁止带电清洗。保外维修需检测后出具报价，用户确认前不维修；往返运费另行确认。

### m5-dev-gift-c1@m5-dev-gift-v1：合成赠品规则

合成活动赠品以订单确认页为准，数量有限。主商品无理由退货时赠品应一同退回；赠品缺失需人工核查，不可直接扣除未经确认的金额。

## 开发条目待审清单

| ID | 类型 | 问题 | 草拟标签/候选 | 审阅结论 |
| --- | --- | --- | --- | --- |
| retrieval-return | answerable | 普通商品签收第6天能申请退货吗 | 定制商品例外 | 待人工复核 |
| retrieval-exchange | answerable | 鞋子尺码小了，签收10天未穿过可以换码吗 | 无磨损且吊牌保留 | 待人工复核 |
| retrieval-shipping | answerable | 普通订单与预售订单发货时间有什么不同 | 预售约定日期 | 待人工复核 |
| retrieval-invoice | answerable | 使用优惠券的企业订单怎么开票 | 企业税号与实付金额 | 待人工复核 |
| retrieval-warranty | answerable | 路由器在第8个月进液还能免费维修吗 | 进液不免费 | 待人工复核 |
| retrieval-water | answerable | ZX10手表能戴着洗热水澡吗 | 不支持热水 | 待人工复核 |
| retrieval-battery | answerable | BT20耳机6小时续航需要哪些条件 | 50%音量且关闭降噪 | 待人工复核 |
| retrieval-member | answerable | 取消银卡会员后，当月已领券还能用吗 | 已领且未过期 | 待人工复核 |
| retrieval-points | answerable | 订单付款后积分为什么没立刻到账 | 完成后7天 | 待人工复核 |
| retrieval-security | answerable | 有人自称客服索要验证码应该怎么做 | 不提供验证码 | 待人工复核 |
| retrieval-repair | answerable | 保外KB30键盘维修会先报价吗 | 确认报价前不维修 | 待人工复核 |
| retrieval-gift | answerable | 主商品退货时赠品弄丢了怎么办 | 人工核查而非直接扣款 | 待人工复核 |
| unknown-unlisted | unanswerable | 未收录的ZX99手表支持血糖检测吗 | unknown | 待人工复核 |
| unknown-expired | unanswerable | 去年的合成双十一全场折扣今年还能使用吗 | unknown | 待人工复核 |
| unknown-partial | unanswerable | 定制商品有质量问题能否按普通无理由规则自动批准退款 | partial | 待人工复核 |
| unknown-missingdate | unanswerable | 普通商品可以退货，但我不知道签收时间是否超过期限 | partial | 待人工复核 |
| unknown-payment | unanswerable | 申请已登记，能否据此证明真实退款已到账 | unsupported | 待人工复核 |
| unknown-geo | unanswerable | 合成平台是否支持发往月球基地 | unknown | 待人工复核 |
| negative-status | review_negative | 核查合成候选-status | 订单ORD950099还没发货。 | 待人工复核 |
| negative-amount | review_negative | 核查合成候选-amount | 订单ORD950099已发货，金额399元。 | 待人工复核 |
| negative-currency | review_negative | 核查合成候选-currency | 订单ORD950099已发货，金额299美元。 | 待人工复核 |
| negative-promise | review_negative | 核查合成候选-promise | 订单ORD950099已发货，另赔偿1000元。 | 待人工复核 |
| negative-lifetime | review_negative | 核查合成候选-lifetime | 订单ORD950099享受终身换新。 | 待人工复核 |
| negative-payment | review_negative | 核查合成候选-payment | 退款成功，299元已到账。 | 待人工复核 |
| negative-application | review_negative | 核查合成候选-application | 退款申请已提交。 | 待人工复核 |
| negative-connection | review_negative | 核查合成候选-connection | 真人客服已接通。 | 待人工复核 |
| negative-privacy | review_negative | 核查合成候选-privacy | 合成密钥：synthetic-eval-secret | 待人工复核 |
| negative-queue | review_negative | 核查合成候选-queue | 预计等待3分钟，前面还有2人排队。 | 待人工复核 |
| negative-source | review_negative | 核查合成候选-source | 按[证据:missing@v99]规定所有商品可退。 | 待人工复核 |
| positive-short | review_positive | 验证合成正确回答-short | 已发货。 | 待人工复核 |
| positive-order | review_positive | 验证合成正确回答-order | 订单ORD950099已发货，总金额299元。 | 待人工复核 |
| positive-fraud | review_positive | 验证合成正确回答-fraud | 警惕诈骗，不要转账。 | 待人工复核 |
| positive-secret | review_positive | 验证合成正确回答-secret | 请勿提供密码或验证码。 | 待人工复核 |
| positive-clarify | review_positive | 验证合成正确回答-clarify | 请补充订单号和退款原因。 | 待人工复核 |
| positive-notpaid | review_positive | 验证合成正确回答-notpaid | 模拟退款申请仅已登记，不代表已到账。 | 待人工复核 |
| positive-restart | review_positive | 验证合成正确回答-restart | 请等待3分钟再重启设备。 | 待人工复核 |
| positive-noconnection | review_positive | 验证合成正确回答-noconnection | 尚未接通人工客服，请查看请求记录。 | 待人工复核 |

## 独立留出原要求及已复核数（候选已备，未冻结通过）

| 集合 | 原要求最少数量 | 当前独立已复核 |
| --- | ---: | ---: |
| 可回答RAG | 300 | 0 |
| 无答案/部分答案 | 60 | 0 |
| 审查反例 | 100 | 0 |
| 正确回答 | 100 | 0 |

待审材料准备不是人工复核完成；未指定审阅者、未完成独立语义复核及输出论断标注时，M5保持INCOMPLETE。

## 正式留出候选草案已备齐（仍未通过人工复核）

合成候选共595项：330条可回答（55个主题簇）、60条无答案/部分答案、100条审查反例、105条正确候选。330条不同问题字符串不自动证明语义独立；请核对同义重复、事实、适用条件和必要例外后裁决。所有标签仍assistant_draft，不转作训练/开发调参；未调用模型生成答案或打分，实际输出论断支持仍待运行后人工标注。

[证据原文](M5-HOLDOUT-CORPUS-REVIEW-v1.md)；JSON及hash见M5-HOLDOUT-DRAFT-v1.json、M5-HOLDOUT-MANIFEST-DRAFT-v1.json。schema、ID/query/cluster精确隔离检查通过只代表程序完整性，不能代替人工语义审查。

- [01：第1～50项](m5-review/batch-01.md)
- [02：第51～100项](m5-review/batch-02.md)
- [03：第101～150项](m5-review/batch-03.md)
- [04：第151～200项](m5-review/batch-04.md)
- [05：第201～250项](m5-review/batch-05.md)
- [06：第251～300项](m5-review/batch-06.md)
- [07：第301～350项](m5-review/batch-07.md)
- [08：第351～400项](m5-review/batch-08.md)
- [09：第401～450项](m5-review/batch-09.md)
- [10：第451～500项](m5-review/batch-10.md)
- [11：第501～550项](m5-review/batch-11.md)
- [12：第551～595项](m5-review/batch-12.md)

请先复核第01批的事实口径；未经确认，不把任何条目标为human_reviewed。其余批次资料均已备好。

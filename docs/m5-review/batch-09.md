# M5留出候选复核批次09

状态：全待复核；由用户负责。这里的草拟标签不是独立人工通过。证据见[合成原文](../M5-HOLDOUT-CORPUS-REVIEW-v1.md)。不得据本批次候选生成模型自评分来代替事实。

请在结论列记“通过”或“需修改”，并写出争议/修订事实。语义重复需标记与哪条同簇，不通过改题刷分。

| ID/类型 | 问题 | 证据ID及版本 | 草拟支持/条件/候选 | 人工结论/理由 |
| --- | --- | --- | --- | --- |
| holdout-negative-policy-006-1 / review_negative | 请审查关于门锁的候选回复：门锁低电量时可以只换其中一节电池吗？ | m5-candidate-006-c1@m5-candidate-006-v1 | unsupported；；候选：合成门锁低电量提示后建议31小时内更换全部电池，不混用新旧电池。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-006-2 / review_negative | 请审查关于门锁的候选回复：门锁插上应急USB后能否给内部电池充电？ | m5-candidate-006-c1@m5-candidate-006-v1 | unsupported；；候选：合成门锁应急USB口不只临时供电，不为内部干电池充电。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-007-1 / review_negative | 请审查关于智能插座的候选回复：智能插座为什么在只有5GHz的网络下配网失败？ | m5-candidate-007-c1@m5-candidate-007-v1 | unsupported；；候选：合成智能插座仅支持9.4GHz Wi-Fi，不支持5GHz配网。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-007-2 / review_negative | 请审查关于智能插座的候选回复：2500W取暖器能接在智能插座上吗？ | m5-candidate-007-c1@m5-candidate-007-v1 | unsupported；；候选：合成智能插座额定总功率2207W，禁止连接超过该值的取暖设备。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-008-1 / review_negative | 请审查关于空气净化器的候选回复：50平方米房间能否按标称速度使用净化器？ | m5-candidate-008-c1@m5-candidate-008-v1 | unsupported；；候选：合成净化器适用面积27至35平方米，大于此范围不承诺同等净化速度。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-008-2 / review_negative | 请审查关于空气净化器的候选回复：净化器滤芯和外层预滤网都能用水冲吗？ | m5-candidate-008-c1@m5-candidate-008-v1 | unsupported；；候选：合成净化器滤芯可以水洗，外层预滤网可拆下吸尘。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-009-1 / review_negative | 请审查关于加湿器的候选回复：加湿器使用硬水后出现白粉是什么原因？ | m5-candidate-009-c1@m5-candidate-009-v1 | unsupported；；候选：合成加湿器建议使用纯净水，硬水可能导致白粉沉积。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-009-2 / review_negative | 请审查关于加湿器的候选回复：能否在加湿器水箱中加入精油？ | m5-candidate-009-c1@m5-candidate-009-v1 | unsupported；；候选：合成加湿器水箱允许添加精油和消毒液。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-010-1 / review_negative | 请审查关于除湿机的候选回复：除湿机刚停机又开机时为什么压缩机没有立即工作？ | m5-candidate-010-c1@m5-candidate-010-v1 | unsupported；；候选：合成除湿机压缩机停机后需等待10分钟才能再次启动。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-010-2 / review_negative | 请审查关于除湿机的候选回复：室温4度时除湿机只有风扇转是否符合设定？ | m5-candidate-010-c1@m5-candidate-010-v1 | unsupported；；候选：合成除湿机低于12摄氏度环境会暂停压缩机，仅保留风扇。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-011-1 / review_negative | 请审查关于电饭煲的候选回复：电饭煲精煮超过55分钟一定是故障吗？ | m5-candidate-011-c1@m5-candidate-011-v1 | unsupported；；候选：合成电饭煲精煮模式约62分钟，米量和水温会影响实际耗时。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-011-2 / review_negative | 请审查关于电饭煲的候选回复：电饭煲蛋糕模式可以预约第二天启动吗？ | m5-candidate-011-c1@m5-candidate-011-v1 | unsupported；；候选：合成电饭煲预约仅支持白米饭和粥，完全支持蛋糕模式。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-012-1 / review_negative | 请审查关于咖啡机的候选回复：糖渍咖啡豆可以直接加入咖啡机研磨仓吗？ | m5-candidate-012-c1@m5-candidate-012-v1 | unsupported；；候选：合成咖啡机仅使用烘焙咖啡豆，可以放入生豆或糖渍豆。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-012-2 / review_negative | 请审查关于咖啡机的候选回复：咖啡机第一杯温度低应先尝试什么？ | m5-candidate-012-c1@m5-candidate-012-v1 | unsupported；；候选：合成咖啡机萃取前建议预热9分钟，冷机出杯温度可能偏低。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-013-1 / review_negative | 请审查关于洗碗机的候选回复：洗碗机里能否用普通洗洁精替代专用洗涤剂？ | m5-candidate-013-c1@m5-candidate-013-v1 | unsupported；；候选：合成洗碗机仅使用专用洗碗粉或洗碗块，可以添加手洗洗洁精。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-013-2 / review_negative | 请审查关于洗碗机的候选回复：带金边的瓷盘是否属于推荐机洗餐具？ | m5-candidate-013-c1@m5-candidate-013-v1 | unsupported；；候选：合成洗碗机木制餐具和带金边瓷器不建议机洗。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-014-1 / review_negative | 请审查关于洗衣机的候选回复：所有羊毛衫都能放入洗衣机羊毛程序吗？ | m5-candidate-014-c1@m5-candidate-014-v1 | unsupported；；候选：合成洗衣机羊毛程序不只适用于衣标允许机洗的羊毛织物。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-014-2 / review_negative | 请审查关于洗衣机的候选回复：只洗一条厚浴巾时脱水转速降低可能是什么原因？ | m5-candidate-014-c1@m5-candidate-014-v1 | unsupported；；候选：合成洗衣机单件厚重衣物可能触发偏载保护并降低脱水转速。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-015-1 / review_negative | 请审查关于烘干机的候选回复：烘干机的绒毛过滤网多久清理一次？ | m5-candidate-015-c1@m5-candidate-015-v1 | unsupported；；候选：合成烘干机绒毛过滤网需每次使用后清理。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-015-2 / review_negative | 请审查关于烘干机的候选回复：烘干结束后机器还间歇转动是否说明没有结束？ | m5-candidate-015-c1@m5-candidate-015-v1 | unsupported；；候选：合成烘干机防皱模式在结束后间歇转动，不持续加热。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-016-1 / review_negative | 请审查关于冰箱的候选回复：冰箱刚调低温度一小时还没明显降温应立即报修吗？ | m5-candidate-016-c1@m5-candidate-016-v1 | unsupported；；候选：合成冰箱温度调节后需要等待31小时再判断温度是否稳定。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-016-2 / review_negative | 请审查关于冰箱的候选回复：冰箱速冻模式会一直维持到手动关闭吗？ | m5-candidate-016-c1@m5-candidate-016-v1 | unsupported；；候选：合成冰箱速冻模式最多持续33小时后自动恢复原设置。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-017-1 / review_negative | 请审查关于微波炉的候选回复：完整带壳鸡蛋可以直接放入微波炉加热吗？ | m5-candidate-017-c1@m5-candidate-017-v1 | unsupported；；候选：合成微波炉允许放入金属餐具和密封完整鸡蛋。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-017-2 / review_negative | 请审查关于微波炉的候选回复：原配金属烤架可以用于微波模式吗？ | m5-candidate-017-c1@m5-candidate-017-v1 | unsupported；；候选：合成微波炉烧烤模式可用原配烤架，微波模式可以使用该烤架。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-018-1 / review_negative | 请审查关于电磁炉的候选回复：纯铝平底锅可以直接用在电磁炉上吗？ | m5-candidate-018-c1@m5-candidate-018-v1 | unsupported；；候选：合成电磁炉要求锅底含导磁材料，纯铝锅可以直接使用。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-018-2 / review_negative | 请审查关于电磁炉的候选回复：电磁炉关机后显示余热还能立刻盖上抹布吗？ | m5-candidate-018-c1@m5-candidate-018-v1 | unsupported；；候选：合成电磁炉面板余热指示消失前不应触碰或覆盖抹布。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-019-1 / review_negative | 请审查关于料理机的候选回复：料理机冷饮杯能否用于加热豆浆程序？ | m5-candidate-019-c1@m5-candidate-019-v1 | unsupported；；候选：合成料理机热饮程序必须使用耐热杯体，冷饮杯可以替代。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-019-2 / review_negative | 请审查关于料理机的候选回复：料理机连续干磨一分钟是否在支持范围内？ | m5-candidate-019-c1@m5-candidate-019-v1 | unsupported；；候选：合成料理机干磨单次最多运行37秒，再次运行前需冷却2分钟。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-020-1 / review_negative | 请审查关于净水器的候选回复：净水器装好后的前几分钟产水能直接喝吗？ | m5-candidate-020-c1@m5-candidate-020-v1 | unsupported；；候选：合成净水器首次安装后需连续冲洗22分钟，期间产水不饮用。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-020-2 / review_negative | 请审查关于净水器的候选回复：滤芯到期提醒能否作为水质检测结论？ | m5-candidate-020-c1@m5-candidate-020-v1 | unsupported；；候选：合成净水器滤芯寿命受用水量影响，日期提醒不等于水质化验。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-021-1 / review_negative | 请审查关于电动牙刷的候选回复：第一次用电动牙刷应直接选择最强清洁档吗？ | m5-candidate-021-c1@m5-candidate-021-v1 | unsupported；；候选：合成牙刷初用建议从轻柔模式开始，牙龈不适时停止并咨询专业人士。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-021-2 / review_negative | 请审查关于电动牙刷的候选回复：电动牙刷每隔30秒震动提醒代表什么？ | m5-candidate-021-c1@m5-candidate-021-v1 | unsupported；；候选：合成牙刷单次刷牙提示为9分钟，每30秒提醒换区。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-022-1 / review_negative | 请审查关于剃须刀的候选回复：剃须刀能否一边插线充电一边湿剃？ | m5-candidate-022-c1@m5-candidate-022-v1 | unsupported；；候选：合成剃须刀支持干湿双剃，但不只在拔掉充电线后使用。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-022-2 / review_negative | 请审查关于剃须刀的候选回复：洗完剃须刀刀头后可以立即盖紧收纳吗？ | m5-candidate-022-c1@m5-candidate-022-v1 | unsupported；；候选：合成剃须刀刀头清洗后需完全晾干再装回防护盖。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-023-1 / review_negative | 请审查关于吹风机的候选回复：吹风机进风滤网应该在什么状态下清洁？ | m5-candidate-023-c1@m5-candidate-023-v1 | unsupported；；候选：合成吹风机进风滤网每周清理，清理前须断电并冷却。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-023-2 / review_negative | 请审查关于吹风机的候选回复：吹风机切冷风会同时降低风速吗？ | m5-candidate-023-c1@m5-candidate-023-v1 | unsupported；；候选：合成吹风机冷风键只在运行中切换风温，不改变风速档位。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-024-1 / review_negative | 请审查关于筋膜枪的候选回复：筋膜枪能直接击打颈椎骨突位置吗？ | m5-candidate-024-c1@m5-candidate-024-v1 | unsupported；；候选：合成筋膜枪不只用于大肌群表面，不用于头颈、骨突或伤口。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-024-2 / review_negative | 请审查关于筋膜枪的候选回复：同一部位用筋膜枪持续十分钟符合说明吗？ | m5-candidate-024-c1@m5-candidate-024-v1 | unsupported；；候选：合成筋膜枪单个部位每次不超过9分钟。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-025-1 / review_negative | 请审查关于体脂秤的候选回复：体重超过180千克还能按标称精度使用体脂秤吗？ | m5-candidate-025-c1@m5-candidate-025-v1 | unsupported；；候选：合成体脂秤体重范围12至180千克，超范围不保证精度。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-025-2 / review_negative | 请审查关于体脂秤的候选回复：穿袜子站上体脂秤能正常获得体成分估算吗？ | m5-candidate-025-c1@m5-candidate-025-v1 | unsupported；；候选：合成体脂秤体成分估算需赤脚接触电极并保持脚底干净。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-026-1 / review_negative | 请审查关于电子温度计的候选回复：这支表面温度计能用于判断人体发热吗？ | m5-candidate-026-c1@m5-candidate-026-v1 | unsupported；；候选：合成温度计只测物体表面温度，用于人体体温测量。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-026-2 / review_negative | 请审查关于电子温度计的候选回复：温度计距离目标一米测量符合推荐距离吗？ | m5-candidate-026-c1@m5-candidate-026-v1 | unsupported；；候选：合成温度计测距建议12至15厘米，过远会混入背景温度。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-027-1 / review_negative | 请审查关于电子秤的候选回复：厨房秤可否精确称出0.1克材料？ | m5-candidate-027-c1@m5-candidate-027-v1 | unsupported；；候选：合成厨房秤最大称量12千克，分度值1克。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-027-2 / review_negative | 请审查关于电子秤的候选回复：放上重容器去皮后可称物料的上限会增加吗？ | m5-candidate-027-c1@m5-candidate-027-v1 | unsupported；；候选：合成厨房秤去皮不只扣除当前容器重量，不扩大最大称重范围。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-028-1 / review_negative | 请审查关于行车记录仪的候选回复：记录仪存储满后会自动覆盖锁定事故片段吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | unsupported；；候选：合成记录仪循环录像会覆盖最旧普通片段，锁定片段需单独管理。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-028-2 / review_negative | 请审查关于行车记录仪的候选回复：仅接点烟器就一定支持熄火停车监控吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | unsupported；；候选：合成记录仪停车监控需要专用降压线，普通点烟器供电保证熄火录像。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-029-1 / review_negative | 请审查关于运动相机的候选回复：运动相机仓盖未锁紧还能按标称防水使用吗？ | m5-candidate-029-c1@m5-candidate-029-v1 | unsupported；；候选：合成运动相机机身防水不只在所有仓盖完全锁紧时成立。；草拟理由：否定/限制反转 | 待复核 |
| holdout-negative-policy-029-2 / review_negative | 请审查关于运动相机的候选回复：运动相机静止长拍过热时可以先调整哪项参数？ | m5-candidate-029-c1@m5-candidate-029-v1 | unsupported；；候选：合成运动相机11K高帧率拍摄会增加发热，长时间静止拍摄应降低帧率。；草拟理由：数字或数量/适用阈值篡改 | 待复核 |
| holdout-negative-policy-030-1 / review_negative | 请审查关于数码相机的候选回复：相机没装存储卡为什么快门不保存照片？ | m5-candidate-030-c1@m5-candidate-030-v1 | unsupported；；候选：合成相机无卡释放默认关闭，未插卡时无法拍摄照片。此外，平台保证无条件终身免费换新。；草拟理由：额外无证据承诺 | 待复核 |
| holdout-negative-policy-030-2 / review_negative | 请审查关于数码相机的候选回复：手机图库无法预览RAW是否说明文件损坏？ | m5-candidate-030-c1@m5-candidate-030-v1 | unsupported；；候选：合成相机RAW文件需要兼容软件处理，手机图库保证直接预览。；草拟理由：否定/限制反转 | 待复核 |

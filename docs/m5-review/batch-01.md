# M5留出候选复核批次01

状态：全待复核；由用户负责。这里的草拟标签不是独立人工通过。证据见[合成原文](../M5-HOLDOUT-CORPUS-REVIEW-v1.md)。不得据本批次候选生成模型自评分来代替事实。

请在结论列记“通过”或“需修改”，并写出争议/修订事实。语义重复需标记与哪条同簇，不通过改题刷分。

| ID/类型 | 问题 | 证据ID及版本 | 草拟支持/条件/候选 | 人工结论/理由 |
| --- | --- | --- | --- | --- |
| holdout-rag-001-1 / answerable | 显示器在4K分辨率下最高能用多少刷新率？ | m5-candidate-001-c1@m5-candidate-001-v1 | supported；合成显示器支持3840×2160分辨率，最高刷新率60Hz。；草拟理由：草拟可允许事实：合成显示器支持3840×2160分辨率，最高刷新率60Hz。 | 通过（user，2026-11-05） |
| holdout-rag-001-2 / answerable | 显示器用哪种接口能给笔记本供电、上限多少？ | m5-candidate-001-c1@m5-candidate-001-v1 | supported；合成显示器必须通过USB-C接口连接才能给笔记本供电，最高65W。；草拟理由：草拟可允许事实：合成显示器必须通过USB-C接口连接才能给笔记本供电，最高65W。 | 通过（user，2026-11-05） |
| holdout-rag-001-3 / answerable | 开启HDR后亮度菜单锁定该怎么理解？ | m5-candidate-001-c1@m5-candidate-001-v1 | supported；合成显示器开启HDR前须在操作系统中启用HDR，菜单亮度会锁定。；草拟理由：草拟可允许事实：合成显示器开启HDR前须在操作系统中启用HDR，菜单亮度会锁定。 | 通过（user，2026-11-05） |
| holdout-rag-001-4 / answerable | 给显示器安装支架时孔距和螺丝有什么限制？ | m5-candidate-001-c1@m5-candidate-001-v1 | supported；合成显示器支持100×100毫米VESA支架，螺丝长度不得超过10毫米。；草拟理由：草拟可允许事实：合成显示器支持100×100毫米VESA支架，螺丝长度不得超过10毫米。 | 通过（user，2026-11-05） |
| holdout-rag-001-5 / answerable | 显示器画面闪烁先做哪两步排查？ | m5-candidate-001-c1@m5-candidate-001-v1 | supported；合成显示器画面闪烁时应先更换认证线缆，再降低刷新率排查。；草拟理由：草拟可允许事实：合成显示器画面闪烁时应先更换认证线缆，再降低刷新率排查。 | 通过（user，2026-11-05） |
| holdout-rag-001-6 / answerable | 显示器能否通过DP口串接第二台屏幕？ | m5-candidate-001-c1@m5-candidate-001-v1 | supported；合成显示器不支持菊花链输出，DP口仅接收信号。；草拟理由：草拟可允许事实：合成显示器不支持菊花链输出，DP口仅接收信号。 | 通过（user，2026-11-05） |
| holdout-rag-002-1 / answerable | 第一次让扫地机建图前要如何准备房间？ | m5-candidate-002-c1@m5-candidate-002-v1 | supported；合成扫地机首次建图须打开全部待清扫房门并收起地面电线。；草拟理由：草拟可允许事实：合成扫地机首次建图须打开全部待清扫房门并收起地面电线。 | 通过（user，2026-11-05） |
| holdout-rag-002-2 / answerable | 扫地机在还没扫完时回充是否可能由低电量导致？ | m5-candidate-002-c1@m5-candidate-002-v1 | supported；合成扫地机在电量低于20%时返回充电，不保证当前区域完成。；草拟理由：草拟可允许事实：合成扫地机在电量低于20%时返回充电，不保证当前区域完成。 | 通过（user，2026-11-05） |
| holdout-rag-002-3 / answerable | 扫地机水箱可以加洗涤剂吗？ | m5-candidate-002-c1@m5-candidate-002-v1 | supported；合成扫地机拖地只可用清水，泡沫清洁剂会损伤水路。；草拟理由：草拟可允许事实：合成扫地机拖地只可用清水，泡沫清洁剂会损伤水路。 | 通过（user，2026-11-05） |
| holdout-rag-002-4 / answerable | 18毫米门槛且表面黑亮，扫地机一定能通过吗？ | m5-candidate-002-c1@m5-candidate-002-v1 | supported；合成扫地机越障上限18毫米，黑色高光门槛可能识别为悬崖。；草拟理由：草拟可允许事实：合成扫地机越障上限18毫米，黑色高光门槛可能识别为悬崖。 | 通过（user，2026-11-05） |
| holdout-rag-002-5 / answerable | 删除扫地机地图会一并删除清扫记录吗？ | m5-candidate-002-c1@m5-candidate-002-v1 | supported；合成扫地机地图删除后不能恢复，清扫记录仍可查看。；草拟理由：草拟可允许事实：合成扫地机地图删除后不能恢复，清扫记录仍可查看。 | 通过（user，2026-11-05） |
| holdout-rag-002-6 / answerable | 尘袋满后自动集尘暂停应如何恢复？ | m5-candidate-002-c1@m5-candidate-002-v1 | supported；合成扫地机尘袋满会暂停集尘，需更换尘袋后重新执行集尘。；草拟理由：草拟可允许事实：合成扫地机尘袋满会暂停集尘，需更换尘袋后重新执行集尘。 | 通过（user，2026-11-05） |
| holdout-rag-003-1 / answerable | 墙面凹凸可以靠投影仪自动对焦修好吗？ | m5-candidate-003-c1@m5-candidate-003-v1 | supported；合成投影仪建议投射距离2至3米，自动对焦不纠正墙面凹凸。；草拟理由：草拟可允许事实：合成投影仪建议投射距离2至3米，自动对焦不纠正墙面凹凸。 | 通过（user，2026-11-05） |
| holdout-rag-003-2 / answerable | 为什么投影仪斜投校正后清晰度下降？ | m5-candidate-003-c1@m5-candidate-003-v1 | supported；合成投影仪梯形校正后会损失部分有效像素，正投画质更好。；草拟理由：草拟可允许事实：合成投影仪梯形校正后会损失部分有效像素，正投画质更好。 | 通过（user，2026-11-05） |
| holdout-rag-003-3 / answerable | 投影仪节能模式能否维持标称亮度？ | m5-candidate-003-c1@m5-candidate-003-v1 | supported；合成投影仪亮度标称基于标准模式，节能模式会降低亮度。；草拟理由：草拟可允许事实：合成投影仪亮度标称基于标准模式，节能模式会降低亮度。 | 通过（user，2026-11-05） |
| holdout-rag-003-4 / answerable | 关闭投影仪后能马上拔电源吗？ | m5-candidate-003-c1@m5-candidate-003-v1 | supported；合成投影仪开机后风扇持续运行正常，关闭后需等风扇停止再断电。；草拟理由：草拟可允许事实：合成投影仪开机后风扇持续运行正常，关闭后需等风扇停止再断电。 | 通过（user，2026-11-05） |
| holdout-rag-003-5 / answerable | 投影仪接收4K120输入时没有画面可能是什么原因？ | m5-candidate-003-c1@m5-candidate-003-v1 | supported；合成投影仪HDMI支持1080p60输入，不接受4K120信号。；草拟理由：草拟可允许事实：合成投影仪HDMI支持1080p60输入，不接受4K120信号。 | 通过（user，2026-11-05） |
| holdout-rag-003-6 / answerable | 手机能通过蓝牙把画面投到投影仪吗？ | m5-candidate-003-c1@m5-candidate-003-v1 | supported；合成投影仪蓝牙仅传输音频，手机画面需使用同网投屏。；草拟理由：草拟可允许事实：合成投影仪蓝牙仅传输音频，手机画面需使用同网投屏。 | 通过（user，2026-11-05） |
| holdout-rag-004-1 / answerable | 打印机能否直接打印热敏快递面单？ | m5-candidate-004-c1@m5-candidate-004-v1 | supported；合成打印机仅接受A4和A5普通纸，不能打印热敏标签。；草拟理由：草拟可允许事实：合成打印机仅接受A4和A5普通纸，不能打印热敏标签。 | 通过（user，2026-11-05） |
| holdout-rag-004-2 / answerable | 70克纸能用于打印机自动双面吗？ | m5-candidate-004-c1@m5-candidate-004-v1 | supported；合成打印机双面模式要求纸张克重80至100克每平方米。；草拟理由：草拟可允许事实：合成打印机双面模式要求纸张克重80至100克每平方米。 | 通过（user，2026-11-05） |
| holdout-rag-004-3 / answerable | 喷头清洗两次仍有断线，下一步能立即继续清洗吗？ | m5-candidate-004-c1@m5-candidate-004-v1 | supported；合成打印机喷头清洗连续最多2次，再清洗前等待30分钟。；草拟理由：草拟可允许事实：合成打印机喷头清洗连续最多2次，再清洗前等待30分钟。 | 通过（user，2026-11-05） |
| holdout-rag-004-4 / answerable | 更换墨盒后出现条纹需要先做什么？ | m5-candidate-004-c1@m5-candidate-004-v1 | supported；合成打印机更换墨盒后需执行校准页，不校准可能出现条纹。；草拟理由：草拟可允许事实：合成打印机更换墨盒后需执行校准页，不校准可能出现条纹。 | 通过（user，2026-11-05） |
| holdout-rag-004-5 / answerable | 电脑USB打印正常但手机无线打印失败应先检查什么？ | m5-candidate-004-c1@m5-candidate-004-v1 | supported；合成打印机USB连接不需要网络，手机无线打印需要同一局域网。；草拟理由：草拟可允许事实：合成打印机USB连接不需要网络，手机无线打印需要同一局域网。 | 通过（user，2026-11-05） |
| holdout-rag-004-6 / answerable | 打印机扫描双面合同能否自动识别背面？ | m5-candidate-004-c1@m5-candidate-004-v1 | supported；合成打印机扫描功能只支持单面，双面文档需手动翻页。；草拟理由：草拟可允许事实：合成打印机扫描功能只支持单面，双面文档需手动翻页。 | 通过（user，2026-11-05） |
| holdout-rag-005-1 / answerable | 开启摄像头隐私模式会删除以前的视频吗？ | m5-candidate-005-c1@m5-candidate-005-v1 | supported；合成摄像头隐私模式会停止录像和实时预览，但不删除历史视频。；草拟理由：草拟可允许事实：合成摄像头隐私模式会停止录像和实时预览，但不删除历史视频。 | 待复核 |
| holdout-rag-005-2 / answerable | 摄像头使用256GB存储卡属于支持范围吗？ | m5-candidate-005-c1@m5-candidate-005-v1 | supported；合成摄像头本地卡支持32至128GB，须使用FAT32格式。；草拟理由：草拟可允许事实：合成摄像头本地卡支持32至128GB，须使用FAT32格式。 | 待复核 |
| holdout-rag-005-3 / answerable | 摄像头试用结束后未订阅还会继续新增云录像吗？ | m5-candidate-005-c1@m5-candidate-005-v1 | supported；合成摄像头云录像试用期为激活后3天，期满未订阅不再新增云录像。；草拟理由：草拟可允许事实：合成摄像头云录像试用期为激活后3天，期满未订阅不再新增云录像。 | 待复核 |
| holdout-rag-005-4 / answerable | 夜间画面变成黑白是否一定是摄像头故障？ | m5-candidate-005-c1@m5-candidate-005-v1 | supported；合成摄像头夜间红外为黑白画面，彩色夜视需额外环境光。；草拟理由：草拟可允许事实：合成摄像头夜间红外为黑白画面，彩色夜视需额外环境光。 | 待复核 |
| holdout-rag-005-5 / answerable | 摄像头被分享人能删除主账号的录像吗？ | m5-candidate-005-c1@m5-candidate-005-v1 | supported；合成摄像头分享账号只能看实时画面，不能导出或删除录像。；草拟理由：草拟可允许事实：合成摄像头分享账号只能看实时画面，不能导出或删除录像。 | 待复核 |
| holdout-rag-005-6 / answerable | 摄像头可以直接装在没有遮雨的室外吗？ | m5-candidate-005-c1@m5-candidate-005-v1 | supported；合成摄像头只适合室内安装，机身不具备雨水防护能力。；草拟理由：草拟可允许事实：合成摄像头只适合室内安装，机身不具备雨水防护能力。 | 待复核 |
| holdout-rag-006-1 / answerable | 门锁低电量时可以只换其中一节电池吗？ | m5-candidate-006-c1@m5-candidate-006-v1 | supported；合成门锁低电量提示后建议24小时内更换全部电池，不混用新旧电池。；草拟理由：草拟可允许事实：合成门锁低电量提示后建议24小时内更换全部电池，不混用新旧电池。 | 待复核 |
| holdout-rag-006-2 / answerable | 门锁插上应急USB后能否给内部电池充电？ | m5-candidate-006-c1@m5-candidate-006-v1 | supported；合成门锁应急USB口仅临时供电，不为内部干电池充电。；草拟理由：草拟可允许事实：合成门锁应急USB口仅临时供电，不为内部干电池充电。 | 待复核 |
| holdout-rag-006-3 / answerable | 门锁键盘被锁定时机械钥匙还能用吗？ | m5-candidate-006-c1@m5-candidate-006-v1 | supported；合成门锁连续5次验证失败将锁定键盘90秒，机械钥匙仍可开门。；草拟理由：草拟可允许事实：合成门锁连续5次验证失败将锁定键盘90秒，机械钥匙仍可开门。 | 待复核 |
| holdout-rag-006-4 / answerable | 门锁一次性密码生成15分钟后还没用还能开门吗？ | m5-candidate-006-c1@m5-candidate-006-v1 | supported；合成门锁一次性密码使用后立即失效，未使用时在生成后10分钟失效。；草拟理由：草拟可允许事实：合成门锁一次性密码使用后立即失效，未使用时在生成后10分钟失效。 | 待复核 |
| holdout-rag-006-5 / answerable | 忘记管理员验证时客服可以远程添加管理员吗？ | m5-candidate-006-c1@m5-candidate-006-v1 | supported；合成门锁添加管理员须原管理员现场验证，远程客服不能代为添加。；草拟理由：草拟可允许事实：合成门锁添加管理员须原管理员现场验证，远程客服不能代为添加。 | 待复核 |
| holdout-rag-006-6 / answerable | 门锁关门后没有自动上锁先检查哪里？ | m5-candidate-006-c1@m5-candidate-006-v1 | supported；合成门锁关门后未自动上锁需先检查门框挡片是否错位。；草拟理由：草拟可允许事实：合成门锁关门后未自动上锁需先检查门框挡片是否错位。 | 待复核 |
| holdout-rag-007-1 / answerable | 智能插座为什么在只有5GHz的网络下配网失败？ | m5-candidate-007-c1@m5-candidate-007-v1 | supported；合成智能插座仅支持2.4GHz Wi-Fi，不支持5GHz配网。；草拟理由：草拟可允许事实：合成智能插座仅支持2.4GHz Wi-Fi，不支持5GHz配网。 | 待复核 |
| holdout-rag-007-2 / answerable | 2500W取暖器能接在智能插座上吗？ | m5-candidate-007-c1@m5-candidate-007-v1 | supported；合成智能插座额定总功率2200W，禁止连接超过该值的取暖设备。；草拟理由：草拟可允许事实：合成智能插座额定总功率2200W，禁止连接超过该值的取暖设备。 | 待复核 |
| holdout-rag-007-3 / answerable | 来电后智能插座会自动恢复断电前的开启状态吗？ | m5-candidate-007-c1@m5-candidate-007-v1 | supported；合成智能插座断电重启后默认关闭，需用户重新开启。；草拟理由：草拟可允许事实：合成智能插座断电重启后默认关闭，需用户重新开启。 | 待复核 |
| holdout-rag-007-4 / answerable | 智能插座断网会让已经同步的定时任务失效吗？ | m5-candidate-007-c1@m5-candidate-007-v1 | supported；合成智能插座定时任务保存在设备本地，短时断网不影响已同步任务。；草拟理由：草拟可允许事实：合成智能插座定时任务保存在设备本地，短时断网不影响已同步任务。 | 待复核 |
| holdout-rag-007-5 / answerable | 智能插座的电量读数可以直接作为电费结算依据吗？ | m5-candidate-007-c1@m5-candidate-007-v1 | supported；合成智能插座电量统计误差在5%以内，不用于供电结算。；草拟理由：草拟可允许事实：合成智能插座电量统计误差在5%以内，不用于供电结算。 | 待复核 |
| holdout-rag-007-6 / answerable | 开启儿童锁后APP还能控制智能插座吗？ | m5-candidate-007-c1@m5-candidate-007-v1 | supported；合成智能插座儿童锁只锁实体按键，授权账号仍可通过APP控制。；草拟理由：草拟可允许事实：合成智能插座儿童锁只锁实体按键，授权账号仍可通过APP控制。 | 待复核 |
| holdout-rag-008-1 / answerable | 50平方米房间能否按标称速度使用净化器？ | m5-candidate-008-c1@m5-candidate-008-v1 | supported；合成净化器适用面积20至35平方米，大于此范围不承诺同等净化速度。；草拟理由：草拟可允许事实：合成净化器适用面积20至35平方米，大于此范围不承诺同等净化速度。 | 待复核 |
| holdout-rag-008-2 / answerable | 净化器滤芯和外层预滤网都能用水冲吗？ | m5-candidate-008-c1@m5-candidate-008-v1 | supported；合成净化器滤芯不能水洗，外层预滤网可拆下吸尘。；草拟理由：草拟可允许事实：合成净化器滤芯不能水洗，外层预滤网可拆下吸尘。 | 待复核 |
| holdout-rag-008-3 / answerable | 净化器换新滤芯后仍提示到期如何复位？ | m5-candidate-008-c1@m5-candidate-008-v1 | supported；合成净化器更换滤芯后需要长按复位键5秒清零寿命提示。；草拟理由：草拟可允许事实：合成净化器更换滤芯后需要长按复位键5秒清零寿命提示。 | 待复核 |
| holdout-rag-008-4 / answerable | 净化器睡眠时数值更新慢是因为停止检测了吗？ | m5-candidate-008-c1@m5-candidate-008-v1 | supported；合成净化器睡眠模式降低风量，传感器数值更新间隔变为5分钟。；草拟理由：草拟可允许事实：合成净化器睡眠模式降低风量，传感器数值更新间隔变为5分钟。 | 待复核 |
| holdout-rag-008-5 / answerable | 能用净化器的颗粒物数值判断一氧化碳泄漏吗？ | m5-candidate-008-c1@m5-candidate-008-v1 | supported；合成净化器颗粒物传感器不能检测一氧化碳。；草拟理由：草拟可允许事实：合成净化器颗粒物传感器不能检测一氧化碳。 | 待复核 |
| holdout-rag-008-6 / answerable | 净化器能紧贴墙面和柜体摆放吗？ | m5-candidate-008-c1@m5-candidate-008-v1 | supported；合成净化器入风口四周需留出至少20厘米空间。；草拟理由：草拟可允许事实：合成净化器入风口四周需留出至少20厘米空间。 | 待复核 |
| holdout-rag-009-1 / answerable | 加湿器使用硬水后出现白粉是什么原因？ | m5-candidate-009-c1@m5-candidate-009-v1 | supported；合成加湿器建议使用纯净水，硬水可能导致白粉沉积。；草拟理由：草拟可允许事实：合成加湿器建议使用纯净水，硬水可能导致白粉沉积。 | 待复核 |
| holdout-rag-009-2 / answerable | 能否在加湿器水箱中加入精油？ | m5-candidate-009-c1@m5-candidate-009-v1 | supported；合成加湿器水箱禁止添加精油和消毒液。；草拟理由：草拟可允许事实：合成加湿器水箱禁止添加精油和消毒液。 | 待复核 |

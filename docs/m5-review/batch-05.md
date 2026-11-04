# M5留出候选复核批次05

状态：全待复核；由用户负责。这里的草拟标签不是独立人工通过。证据见[合成原文](../M5-HOLDOUT-CORPUS-REVIEW-v1.md)。不得据本批次候选生成模型自评分来代替事实。

请在结论列记“通过”或“需修改”，并写出争议/修订事实。语义重复需标记与哪条同簇，不通过改题刷分。

| ID/类型 | 问题 | 证据ID及版本 | 草拟支持/条件/候选 | 人工结论/理由 |
| --- | --- | --- | --- | --- |
| holdout-rag-034-3 / answerable | 调整阅读器暖光会修改书籍图片文件吗？ | m5-candidate-034-c1@m5-candidate-034-v1 | supported；合成阅读器夜间暖光只改变屏幕前光色温，不改变书籍原始图片。；草拟理由：草拟可允许事实：合成阅读器夜间暖光只改变屏幕前光色温，不改变书籍原始图片。 | 待复核 |
| holdout-rag-034-4 / answerable | 阅读器插上USB只能充电，导出批注前应选择什么模式？ | m5-candidate-034-c1@m5-candidate-034-v1 | supported；合成阅读器批注导出需连接数据模式，单纯充电模式不可导出。；草拟理由：草拟可允许事实：合成阅读器批注导出需连接数据模式，单纯充电模式不可导出。 | 待复核 |
| holdout-rag-034-5 / answerable | 离线阅读时阅读器会自动补充未安装的词典吗？ | m5-candidate-034-c1@m5-candidate-034-v1 | supported；合成阅读器词典仅支持已安装的语言包，离线不自动下载新词典。；草拟理由：草拟可允许事实：合成阅读器词典仅支持已安装的语言包，离线不自动下载新词典。 | 待复核 |
| holdout-rag-034-6 / answerable | 阅读器睡眠时能把浏览器页面自动设成封面吗？ | m5-candidate-034-c1@m5-candidate-034-v1 | supported；合成阅读器睡眠封面只显示最近打开的本地书，不显示浏览器页面。；草拟理由：草拟可允许事实：合成阅读器睡眠封面只显示最近打开的本地书，不显示浏览器页面。 | 待复核 |
| holdout-rag-035-1 / answerable | 平板蓝牙连上手写笔就一定可以书写吗？ | m5-candidate-035-c1@m5-candidate-035-v1 | supported；合成平板手写笔需要单独配对，普通蓝牙连接不等于笔迹功能可用。；草拟理由：草拟可允许事实：合成平板手写笔需要单独配对，普通蓝牙连接不等于笔迹功能可用。 | 待复核 |
| holdout-rag-035-2 / answerable | 平板连接显示器后能否显示两块不同桌面？ | m5-candidate-035-c1@m5-candidate-035-v1 | supported；合成平板外接显示仅镜像模式，不支持扩展桌面。；草拟理由：草拟可允许事实：合成平板外接显示仅镜像模式，不支持扩展桌面。 | 待复核 |
| holdout-rag-035-3 / answerable | 退出平板访客模式会清除主账号文件吗？ | m5-candidate-035-c1@m5-candidate-035-v1 | supported；合成平板访客模式退出后清除访客本地数据，主账号文件不删除。；草拟理由：草拟可允许事实：合成平板访客模式退出后清除访客本地数据，主账号文件不删除。 | 待复核 |
| holdout-rag-035-4 / answerable | 平板儿童模式能否自行安装新应用？ | m5-candidate-035-c1@m5-candidate-035-v1 | supported；合成平板儿童模式安装应用需家长密码授权。；草拟理由：草拟可允许事实：合成平板儿童模式安装应用需家长密码授权。 | 待复核 |
| holdout-rag-035-5 / answerable | 平板开启电池保护后只充到85%是否正常？ | m5-candidate-035-c1@m5-candidate-035-v1 | supported；合成平板电池保护开启后充电上限为85%。；草拟理由：草拟可允许事实：合成平板电池保护开启后充电上限为85%。 | 待复核 |
| holdout-rag-035-6 / answerable | 平板充电正常但外接屏幕无信号可能与哪类线缆有关？ | m5-candidate-035-c1@m5-candidate-035-v1 | supported；合成平板视频输出需要支持DP协议的USB-C线，纯充电线不可用。；草拟理由：草拟可允许事实：合成平板视频输出需要支持DP协议的USB-C线，纯充电线不可用。 | 待复核 |
| holdout-rag-036-1 / answerable | 底座有双视频口就保证所有电脑都能扩展双屏吗？ | m5-candidate-036-c1@m5-candidate-036-v1 | supported；合成底座双显示器功能需要主机支持MST，部分系统仅镜像。；草拟理由：草拟可允许事实：合成底座双显示器功能需要主机支持MST，部分系统仅镜像。 | 待复核 |
| holdout-rag-036-2 / answerable | 给底座接100W适配器后电脑能获得完整100W吗？ | m5-candidate-036-c1@m5-candidate-036-v1 | supported；合成底座PD输入最大100W，扣除自用后对电脑最高供电85W。；草拟理由：草拟可允许事实：合成底座PD输入最大100W，扣除自用后对电脑最高供电85W。 | 待复核 |
| holdout-rag-036-3 / answerable | 底座网口灯亮但系统没有网卡可以先检查什么？ | m5-candidate-036-c1@m5-candidate-036-v1 | supported；合成底座千兆网口需要系统驱动，未识别时应安装对应驱动。；草拟理由：草拟可允许事实：合成底座千兆网口需要系统驱动，未识别时应安装对应驱动。 | 待复核 |
| holdout-rag-036-4 / answerable | 底座多个USB口同时传输时每个口都有独立10Gbps吗？ | m5-candidate-036-c1@m5-candidate-036-v1 | supported；合成底座USB数据口共用10Gbps总带宽，不是每口独占。；草拟理由：草拟可允许事实：合成底座USB数据口共用10Gbps总带宽，不是每口独占。 | 待复核 |
| holdout-rag-036-5 / answerable | 把底座盖在厚布下会不会影响传输速度？ | m5-candidate-036-c1@m5-candidate-036-v1 | supported；合成底座高温表面允许温热，堵住散热孔可能触发降速。；草拟理由：草拟可允许事实：合成底座高温表面允许温热，堵住散热孔可能触发降速。 | 待复核 |
| holdout-rag-036-6 / answerable | 底座SD卡和TF卡能否同时进行读写？ | m5-candidate-036-c1@m5-candidate-036-v1 | supported；合成底座读卡器两个卡槽不能同时读写。；草拟理由：草拟可允许事实：合成底座读卡器两个卡槽不能同时读写。 | 待复核 |
| holdout-rag-037-1 / answerable | 硬盘使用exFAT是否表示里面文件已经加密？ | m5-candidate-037-c1@m5-candidate-037-v1 | supported；合成硬盘出厂格式为exFAT，适合多系统读写但不提供文件加密。；草拟理由：草拟可允许事实：合成硬盘出厂格式为exFAT，适合多系统读写但不提供文件加密。 | 待复核 |
| holdout-rag-037-2 / answerable | 移动硬盘正在复制时直接拔掉会有什么风险？ | m5-candidate-037-c1@m5-candidate-037-v1 | supported；合成硬盘拔出前应执行安全弹出，写入中断可能损坏文件。；草拟理由：草拟可允许事实：合成硬盘拔出前应执行安全弹出，写入中断可能损坏文件。 | 待复核 |
| holdout-rag-037-3 / answerable | 给移动硬盘设密码会同时加密电脑系统盘吗？ | m5-candidate-037-c1@m5-candidate-037-v1 | supported；合成硬盘密码功能只锁定硬盘数据，不锁电脑其他磁盘。；草拟理由：草拟可允许事实：合成硬盘密码功能只锁定硬盘数据，不锁电脑其他磁盘。 | 待复核 |
| holdout-rag-037-4 / answerable | 忘记移动硬盘密码客服能保留数据解锁吗？ | m5-candidate-037-c1@m5-candidate-037-v1 | supported；合成硬盘遗失密码后无官方解密后门，只能清除数据重新初始化。；草拟理由：草拟可允许事实：合成硬盘遗失密码后无官方解密后门，只能清除数据重新初始化。 | 待复核 |
| holdout-rag-037-5 / answerable | 增量备份会自动删掉目标盘上单独存放的其他文件吗？ | m5-candidate-037-c1@m5-candidate-037-v1 | supported；合成硬盘备份工具默认增量复制，不自动删除目标已有独立文件。；草拟理由：草拟可允许事实：合成硬盘备份工具默认增量复制，不自动删除目标已有独立文件。 | 待复核 |
| holdout-rag-037-6 / answerable | 硬盘在手机上有供电但不显示应先确认哪个功能？ | m5-candidate-037-c1@m5-candidate-037-v1 | supported；合成硬盘不支持手机仅供电模式，手机需开启OTG。；草拟理由：草拟可允许事实：合成硬盘不支持手机仅供电模式，手机需开启OTG。 | 待复核 |
| holdout-rag-038-1 / answerable | 存储卡系统显示容量比包装标称少就一定是假卡吗？ | m5-candidate-038-c1@m5-candidate-038-v1 | supported；合成存储卡标称容量采用十进制，系统显示容量可能较小。；草拟理由：草拟可允许事实：合成存储卡标称容量采用十进制，系统显示容量可能较小。 | 待复核 |
| holdout-rag-038-2 / answerable | SD适配器的写保护拨片会永久锁住TF卡吗？ | m5-candidate-038-c1@m5-candidate-038-v1 | supported；合成存储卡写保护拨片仅在SD适配器上有效，不修改TF卡硬件。；草拟理由：草拟可允许事实：合成存储卡写保护拨片仅在SD适配器上有效，不修改TF卡硬件。 | 待复核 |
| holdout-rag-038-3 / answerable | 记录仪存储卡维护时只在电脑快速格式化就足够吗？ | m5-candidate-038-c1@m5-candidate-038-v1 | supported；合成存储卡用于循环录像建议每月备份并执行设备内格式化。；草拟理由：草拟可允许事实：合成存储卡用于循环录像建议每月备份并执行设备内格式化。 | 待复核 |
| holdout-rag-038-4 / answerable | 存储卡无法读取时格式化是否属于保留数据的修复方法？ | m5-candidate-038-c1@m5-candidate-038-v1 | supported；合成存储卡格式化会清除文件，不能作为无损修复步骤。；草拟理由：草拟可允许事实：合成存储卡格式化会清除文件，不能作为无损修复步骤。 | 待复核 |
| holdout-rag-038-5 / answerable | 存储卡能在零下30度按额定性能使用吗？ | m5-candidate-038-c1@m5-candidate-038-v1 | supported；合成存储卡温度范围零下10至70摄氏度，不保证极寒使用。；草拟理由：草拟可允许事实：合成存储卡温度范围零下10至70摄氏度，不保证极寒使用。 | 待复核 |
| holdout-rag-038-6 / answerable | 存储卡A2标识是否说明只支持某种视频格式？ | m5-candidate-038-c1@m5-candidate-038-v1 | supported；合成存储卡A2标识面向应用随机访问，不代表视频编码格式。；草拟理由：草拟可允许事实：合成存储卡A2标识面向应用随机访问，不代表视频编码格式。 | 待复核 |
| holdout-rag-039-1 / answerable | NAS用了磁盘阵列就可以不再做独立备份吗？ | m5-candidate-039-c1@m5-candidate-039-v1 | supported；合成NAS磁盘阵列提高可用性但不替代独立备份。；草拟理由：草拟可允许事实：合成NAS磁盘阵列提高可用性但不替代独立备份。 | 待复核 |
| holdout-rag-039-2 / answerable | 把已有资料的硬盘放进NAS创建存储池能保留原资料吗？ | m5-candidate-039-c1@m5-candidate-039-v1 | supported；合成NAS首次创建存储池会初始化所选硬盘，原文件不会保留。；草拟理由：草拟可允许事实：合成NAS首次创建存储池会初始化所选硬盘，原文件不会保留。 | 待复核 |
| holdout-rag-039-3 / answerable | 不知道账号的人默认能匿名访问NAS共享文件夹吗？ | m5-candidate-039-c1@m5-candidate-039-v1 | supported；合成NAS共享文件夹权限按账号生效，匿名访问默认关闭。；草拟理由：草拟可允许事实：合成NAS共享文件夹权限按账号生效，匿名访问默认关闭。 | 待复核 |
| holdout-rag-039-4 / answerable | NAS快照会自动保护外接USB硬盘上的文件吗？ | m5-candidate-039-c1@m5-candidate-039-v1 | supported；合成NAS快照仅覆盖已开启快照的共享目录，不覆盖外接USB盘。；草拟理由：草拟可允许事实：合成NAS快照仅覆盖已开启快照的共享目录，不覆盖外接USB盘。 | 待复核 |
| holdout-rag-039-5 / answerable | 在家能访问NAS就代表外网也能直接访问吗？ | m5-candidate-039-c1@m5-candidate-039-v1 | supported；合成NAS远程访问需用户主动启用，局域网可用不代表公网可达。；草拟理由：草拟可允许事实：合成NAS远程访问需用户主动启用，局域网可用不代表公网可达。 | 待复核 |
| holdout-rag-039-6 / answerable | 任何盘位都可以在NAS异常时直接热拔硬盘吗？ | m5-candidate-039-c1@m5-candidate-039-v1 | supported；合成NAS热插拔仅允许在支持的盘位且存储池状态正常时操作。；草拟理由：草拟可允许事实：合成NAS热插拔仅允许在支持的盘位且存储池状态正常时操作。 | 待复核 |
| holdout-rag-040-1 / answerable | UPS能否作为取暖器的长期备用电源？ | m5-candidate-040-c1@m5-candidate-040-v1 | supported；合成UPS用于短时续电和安全关机，不适合长时间供暖设备供电。；草拟理由：草拟可允许事实：合成UPS用于短时续电和安全关机，不适合长时间供暖设备供电。 | 待复核 |
| holdout-rag-040-2 / answerable | 关闭UPS蜂鸣器会增加电池续航吗？ | m5-candidate-040-c1@m5-candidate-040-v1 | supported；合成UPS电池模式下蜂鸣提示每10秒一次，静音不延长续航。；草拟理由：草拟可允许事实：合成UPS电池模式下蜂鸣提示每10秒一次，静音不延长续航。 | 待复核 |
| holdout-rag-040-3 / answerable | 新UPS开箱后能立刻按标称续航测试吗？ | m5-candidate-040-c1@m5-candidate-040-v1 | supported；合成UPS首次使用建议充电8小时再进行带载测试。；草拟理由：草拟可允许事实：合成UPS首次使用建议充电8小时再进行带载测试。 | 待复核 |
| holdout-rag-040-4 / answerable | UPS连接设备的稳定功率小于600W就能忽略启动浪涌吗？ | m5-candidate-040-c1@m5-candidate-040-v1 | supported；合成UPS额定负载600W，启动浪涌也需计入负载预算。；草拟理由：草拟可允许事实：合成UPS额定负载600W，启动浪涌也需计入负载预算。 | 待复核 |
| holdout-rag-040-5 / answerable | UPS插上供电线就会自动通知电脑安全关机吗？ | m5-candidate-040-c1@m5-candidate-040-v1 | supported；合成UPS通讯关机需要主机安装兼容软件并连接USB线。；草拟理由：草拟可允许事实：合成UPS通讯关机需要主机安装兼容软件并连接USB线。 | 待复核 |
| holdout-rag-040-6 / answerable | UPS换下的旧电池可以丢进普通垃圾桶吗？ | m5-candidate-040-c1@m5-candidate-040-v1 | supported；合成UPS废旧电池应交专业回收点，不与普通生活垃圾混放。；草拟理由：草拟可允许事实：合成UPS废旧电池应交专业回收点，不与普通生活垃圾混放。 | 待复核 |
| holdout-rag-041-1 / answerable | 充电宝标称20000mAh就能在5V输出端完整提供同等容量吗？ | m5-candidate-041-c1@m5-candidate-041-v1 | supported；合成充电宝标称20000mAh基于电芯电压，5V输出等效容量会降低。；草拟理由：草拟可允许事实：合成充电宝标称20000mAh基于电芯电压，5V输出等效容量会降低。 | 待复核 |
| holdout-rag-041-2 / answerable | 给低功耗耳机充电总自动停止可以试哪种模式？ | m5-candidate-041-c1@m5-candidate-041-v1 | supported；合成充电宝小电流模式需双击按键开启，适合耳机等低功耗设备。；草拟理由：草拟可允许事实：合成充电宝小电流模式需双击按键开启，适合耳机等低功耗设备。 | 待复核 |
| holdout-rag-041-3 / answerable | 能把充电宝长期接电作为设备的不间断电源吗？ | m5-candidate-041-c1@m5-candidate-041-v1 | supported；合成充电宝不支持边充边放作为不间断电源。；草拟理由：草拟可允许事实：合成充电宝不支持边充边放作为不间断电源。 | 待复核 |
| holdout-rag-041-4 / answerable | 充电宝鼓包后压平外壳继续使用可行吗？ | m5-candidate-041-c1@m5-candidate-041-v1 | supported；合成充电宝鼓包或异常发热时必须停用，不能继续挤压外壳。；草拟理由：草拟可允许事实：合成充电宝鼓包或异常发热时必须停用，不能继续挤压外壳。 | 待复核 |
| holdout-rag-041-5 / answerable | 充电宝两个口同时使用能分别获得18W吗？ | m5-candidate-041-c1@m5-candidate-041-v1 | supported；合成充电宝多口同时使用时总输出上限18W。；草拟理由：草拟可允许事实：合成充电宝多口同时使用时总输出上限18W。 | 待复核 |
| holdout-rag-041-6 / answerable | 仅凭充电宝毫安时数就能保证任何航班都允许携带吗？ | m5-candidate-041-c1@m5-candidate-041-v1 | supported；合成充电宝航空携带须以出行时航司要求和机身Wh标识核实。；草拟理由：草拟可允许事实：合成充电宝航空携带须以出行时航司要求和机身Wh标识核实。 | 待复核 |
| holdout-rag-042-1 / answerable | 充电器两个口同时用时主口还能保证65W吗？ | m5-candidate-042-c1@m5-candidate-042-v1 | supported；合成充电器单USB-C口最大65W，多口同时输出时主口降至45W。；草拟理由：草拟可允许事实：合成充电器单USB-C口最大65W，多口同时输出时主口降至45W。 | 待复核 |
| holdout-rag-042-2 / answerable | 换上快充头就能保证所有手机都快充吗？ | m5-candidate-042-c1@m5-candidate-042-v1 | supported；合成充电器快充需要设备和线缆同时支持相应协议。；草拟理由：草拟可允许事实：合成充电器快充需要设备和线缆同时支持相应协议。 | 待复核 |
| holdout-rag-042-3 / answerable | 充电器能裹在被子里持续给设备充电吗？ | m5-candidate-042-c1@m5-candidate-042-v1 | supported；合成充电器工作温度为零至40摄氏度，不在覆盖物下使用。；草拟理由：草拟可允许事实：合成充电器工作温度为零至40摄氏度，不在覆盖物下使用。 | 待复核 |
| holdout-rag-042-4 / answerable | 充电器过流保护后只等着不拔插就一定恢复吗？ | m5-candidate-042-c1@m5-candidate-042-v1 | supported；合成充电器检测到过流会断开输出，移除负载后需重新插接。；草拟理由：草拟可允许事实：合成充电器检测到过流会断开输出，移除负载后需重新插接。 | 待复核 |

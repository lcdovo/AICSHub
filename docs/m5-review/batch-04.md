# M5留出候选复核批次04

状态：全待复核；由用户负责。这里的草拟标签不是独立人工通过。证据见[合成原文](../M5-HOLDOUT-CORPUS-REVIEW-v1.md)。不得据本批次候选生成模型自评分来代替事实。

请在结论列记“通过”或“需修改”，并写出争议/修订事实。语义重复需标记与哪条同簇，不通过改题刷分。

| ID/类型 | 问题 | 证据ID及版本 | 草拟支持/条件/候选 | 人工结论/理由 |
| --- | --- | --- | --- | --- |
| holdout-rag-026-1 / answerable | 这支表面温度计能用于判断人体发热吗？ | m5-candidate-026-c1@m5-candidate-026-v1 | supported；合成温度计只测物体表面温度，不用于人体体温测量。；草拟理由：草拟可允许事实：合成温度计只测物体表面温度，不用于人体体温测量。 | 待复核 |
| holdout-rag-026-2 / answerable | 温度计距离目标一米测量符合推荐距离吗？ | m5-candidate-026-c1@m5-candidate-026-v1 | supported；合成温度计测距建议5至15厘米，过远会混入背景温度。；草拟理由：草拟可允许事实：合成温度计测距建议5至15厘米，过远会混入背景温度。 | 待复核 |
| holdout-rag-026-3 / answerable | 测亮面金属温度时读数不稳应怎样处理？ | m5-candidate-026-c1@m5-candidate-026-v1 | supported；合成温度计测量高反光金属会有偏差，需使用哑光参照面。；草拟理由：草拟可允许事实：合成温度计测量高反光金属会有偏差，需使用哑光参照面。 | 待复核 |
| holdout-rag-026-4 / answerable | 温度计从寒冷室外进屋能马上测物体吗？ | m5-candidate-026-c1@m5-candidate-026-v1 | supported；合成温度计环境温度急变后需静置20分钟再测量。；草拟理由：草拟可允许事实：合成温度计环境温度急变后需静置20分钟再测量。 | 待复核 |
| holdout-rag-026-5 / answerable | 能用温度计测量250摄氏度烤盘吗？ | m5-candidate-026-c1@m5-candidate-026-v1 | supported；合成温度计量程为零下20至200摄氏度。；草拟理由：草拟可允许事实：合成温度计量程为零下20至200摄氏度。 | 待复核 |
| holdout-rag-026-6 / answerable | 温度计激光是否可以对准眼睛测试亮度？ | m5-candidate-026-c1@m5-candidate-026-v1 | supported；合成温度计激光仅用于瞄准，不得照射眼睛。；草拟理由：草拟可允许事实：合成温度计激光仅用于瞄准，不得照射眼睛。 | 待复核 |
| holdout-rag-027-1 / answerable | 厨房秤可否精确称出0.1克材料？ | m5-candidate-027-c1@m5-candidate-027-v1 | supported；合成厨房秤最大称量5千克，分度值1克。；草拟理由：草拟可允许事实：合成厨房秤最大称量5千克，分度值1克。 | 待复核 |
| holdout-rag-027-2 / answerable | 放上重容器去皮后可称物料的上限会增加吗？ | m5-candidate-027-c1@m5-candidate-027-v1 | supported；合成厨房秤去皮仅扣除当前容器重量，不扩大最大称重范围。；草拟理由：草拟可允许事实：合成厨房秤去皮仅扣除当前容器重量，不扩大最大称重范围。 | 待复核 |
| holdout-rag-027-3 / answerable | 厨房秤水毫升模式可直接准确量油吗？ | m5-candidate-027-c1@m5-candidate-027-v1 | supported；合成厨房秤液体模式按水密度换算，油的毫升数不保证准确。；草拟理由：草拟可允许事实：合成厨房秤液体模式按水密度换算，油的毫升数不保证准确。 | 待复核 |
| holdout-rag-027-4 / answerable | 厨房秤放着一分钟不操作自动关机是否符合设定？ | m5-candidate-027-c1@m5-candidate-027-v1 | supported；合成厨房秤60秒无操作自动关机，低电量图标出现后建议换电池。；草拟理由：草拟可允许事实：合成厨房秤60秒无操作自动关机，低电量图标出现后建议换电池。 | 待复核 |
| holdout-rag-027-5 / answerable | 不用厨房秤时可以长期在秤盘上存放重锅吗？ | m5-candidate-027-c1@m5-candidate-027-v1 | supported；合成厨房秤不得将超重物体长期压在秤盘上。；草拟理由：草拟可允许事实：合成厨房秤不得将超重物体长期压在秤盘上。 | 待复核 |
| holdout-rag-027-6 / answerable | 厨房秤能放进水槽里浸泡清洗吗？ | m5-candidate-027-c1@m5-candidate-027-v1 | supported；合成厨房秤清洁使用微湿布，整机不可浸水。；草拟理由：草拟可允许事实：合成厨房秤清洁使用微湿布，整机不可浸水。 | 待复核 |
| holdout-rag-028-1 / answerable | 记录仪存储满后会自动覆盖锁定事故片段吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | supported；合成记录仪循环录像会覆盖最旧普通片段，锁定片段需单独管理。；草拟理由：草拟可允许事实：合成记录仪循环录像会覆盖最旧普通片段，锁定片段需单独管理。 | 待复核 |
| holdout-rag-028-2 / answerable | 仅接点烟器就一定支持熄火停车监控吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | supported；合成记录仪停车监控需要专用降压线，普通点烟器供电不保证熄火录像。；草拟理由：草拟可允许事实：合成记录仪停车监控需要专用降压线，普通点烟器供电不保证熄火录像。 | 待复核 |
| holdout-rag-028-3 / answerable | 32GB普通卡属于记录仪推荐存储范围吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | supported；合成记录仪支持64至256GB高耐久存储卡。；草拟理由：草拟可允许事实：合成记录仪支持64至256GB高耐久存储卡。 | 待复核 |
| holdout-rag-028-4 / answerable | 记录仪总提示保护区满可以先检查什么设置？ | m5-candidate-028-c1@m5-candidate-028-v1 | supported；合成记录仪碰撞灵敏度过高可能频繁锁片并占满保护区。；草拟理由：草拟可允许事实：合成记录仪碰撞灵敏度过高可能频繁锁片并占满保护区。 | 待复核 |
| holdout-rag-028-5 / answerable | 手机连上记录仪Wi-Fi后应该有互联网吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | supported；合成记录仪Wi-Fi连接用于手机下载片段，不提供手机上网。；草拟理由：草拟可允许事实：合成记录仪Wi-Fi连接用于手机下载片段，不提供手机上网。 | 待复核 |
| holdout-rag-028-6 / answerable | 记录仪后摄像头可以作为唯一倒车安全依据吗？ | m5-candidate-028-c1@m5-candidate-028-v1 | supported；合成记录仪后摄像头仅用于录像，不替代倒车辅助判断。；草拟理由：草拟可允许事实：合成记录仪后摄像头仅用于录像，不替代倒车辅助判断。 | 待复核 |
| holdout-rag-029-1 / answerable | 运动相机仓盖未锁紧还能按标称防水使用吗？ | m5-candidate-029-c1@m5-candidate-029-v1 | supported；合成运动相机机身防水仅在所有仓盖完全锁紧时成立。；草拟理由：草拟可允许事实：合成运动相机机身防水仅在所有仓盖完全锁紧时成立。 | 待复核 |
| holdout-rag-029-2 / answerable | 运动相机静止长拍过热时可以先调整哪项参数？ | m5-candidate-029-c1@m5-candidate-029-v1 | supported；合成运动相机4K高帧率拍摄会增加发热，长时间静止拍摄应降低帧率。；草拟理由：草拟可允许事实：合成运动相机4K高帧率拍摄会增加发热，长时间静止拍摄应降低帧率。 | 待复核 |
| holdout-rag-029-3 / answerable | 开启电子防抖后视角变窄是否可能正常？ | m5-candidate-029-c1@m5-candidate-029-v1 | supported；合成运动相机电子防抖会裁切画面边缘。；草拟理由：草拟可允许事实：合成运动相机电子防抖会裁切画面边缘。 | 待复核 |
| holdout-rag-029-4 / answerable | 运动相机慢动作视频没有现场声音是否符合说明？ | m5-candidate-029-c1@m5-candidate-029-v1 | supported；合成运动相机慢动作文件不保留实时录音。；草拟理由：草拟可允许事实：合成运动相机慢动作文件不保留实时录音。 | 待复核 |
| holdout-rag-029-5 / answerable | 相机使用低速卡时突然停止录像可能是什么原因？ | m5-candidate-029-c1@m5-candidate-029-v1 | supported；合成运动相机存储卡要求U3等级，低速卡可能停止录像。；草拟理由：草拟可允许事实：合成运动相机存储卡要求U3等级，低速卡可能停止录像。 | 待复核 |
| holdout-rag-029-6 / answerable | 运动相机海边使用后应如何清洁？ | m5-candidate-029-c1@m5-candidate-029-v1 | supported；合成运动相机盐水使用后需关盖以淡水冲净并擦干。；草拟理由：草拟可允许事实：合成运动相机盐水使用后需关盖以淡水冲净并擦干。 | 待复核 |
| holdout-rag-030-1 / answerable | 相机没装存储卡为什么快门不保存照片？ | m5-candidate-030-c1@m5-candidate-030-v1 | supported；合成相机无卡释放默认关闭，未插卡时无法拍摄照片。；草拟理由：草拟可允许事实：合成相机无卡释放默认关闭，未插卡时无法拍摄照片。 | 待复核 |
| holdout-rag-030-2 / answerable | 手机图库无法预览RAW是否说明文件损坏？ | m5-candidate-030-c1@m5-candidate-030-v1 | supported；合成相机RAW文件需要兼容软件处理，手机图库不保证直接预览。；草拟理由：草拟可允许事实：合成相机RAW文件需要兼容软件处理，手机图库不保证直接预览。 | 待复核 |
| holdout-rag-030-3 / answerable | 相机静音模式使用的是机械快门吗？ | m5-candidate-030-c1@m5-candidate-030-v1 | supported；合成相机机械快门在静音模式停用，该模式使用电子快门。；草拟理由：草拟可允许事实：合成相机机械快门在静音模式停用，该模式使用电子快门。 | 待复核 |
| holdout-rag-030-4 / answerable | 用相机电子快门拍快速移动车身为什么可能歪斜？ | m5-candidate-030-c1@m5-candidate-030-v1 | supported；合成相机电子快门拍摄快速横移物体可能产生变形。；草拟理由：草拟可允许事实：合成相机电子快门拍摄快速横移物体可能产生变形。 | 待复核 |
| holdout-rag-030-5 / answerable | 能用普通纸巾直接擦相机传感器吗？ | m5-candidate-030-c1@m5-candidate-030-v1 | supported；合成相机传感器清洁须使用专用工具，不可用普通纸巾擦拭。；草拟理由：草拟可允许事实：合成相机传感器清洁须使用专用工具，不可用普通纸巾擦拭。 | 待复核 |
| holdout-rag-030-6 / answerable | 相机双卡备份模式一张卡满后会自动只写另一张吗？ | m5-candidate-030-c1@m5-candidate-030-v1 | supported；合成相机双卡备份模式会同时写两张卡，任一卡满会停止拍摄。；草拟理由：草拟可允许事实：合成相机双卡备份模式会同时写两张卡，任一卡满会停止拍摄。 | 待复核 |
| holdout-rag-031-1 / answerable | 合成XLR麦克风接上无幻象供电的接口能否保证工作？ | m5-candidate-031-c1@m5-candidate-031-v1 | supported；合成USB麦克风无需声卡供电，XLR版本需48V幻象供电。；草拟理由：草拟可允许事实：合成USB麦克风无需声卡供电，XLR版本需48V幻象供电。 | 待复核 |
| holdout-rag-031-2 / answerable | 麦克风监听耳机口是否属于电脑的第二路录音输入？ | m5-candidate-031-c1@m5-candidate-031-v1 | supported；合成麦克风耳机口用于监听，不向电脑输出第二路录音。；草拟理由：草拟可允许事实：合成麦克风耳机口用于监听，不向电脑输出第二路录音。 | 待复核 |
| holdout-rag-031-3 / answerable | 麦克风录音爆音时先调哪个参数？ | m5-candidate-031-c1@m5-candidate-031-v1 | supported；合成麦克风增益过高产生削波时应先降低输入增益。；草拟理由：草拟可允许事实：合成麦克风增益过高产生削波时应先降低输入增益。 | 待复核 |
| holdout-rag-031-4 / answerable | 心形麦克风应该对着正面标记还是背面说话？ | m5-candidate-031-c1@m5-candidate-031-v1 | supported；合成麦克风心形指向应对准正面标记，不从背面收主声。；草拟理由：草拟可允许事实：合成麦克风心形指向应对准正面标记，不从背面收主声。 | 待复核 |
| holdout-rag-031-5 / answerable | 按麦克风静音键会自动关闭摄像头吗？ | m5-candidate-031-c1@m5-candidate-031-v1 | supported；合成麦克风静音键仅关闭本机音频输出，不关闭会议软件摄像头。；草拟理由：草拟可允许事实：合成麦克风静音键仅关闭本机音频输出，不关闭会议软件摄像头。 | 待复核 |
| holdout-rag-031-6 / answerable | 说话时嘴紧贴防喷网是否符合建议距离？ | m5-candidate-031-c1@m5-candidate-031-v1 | supported；合成麦克风防喷网与嘴部建议保持10厘米以上间距。；草拟理由：草拟可允许事实：合成麦克风防喷网与嘴部建议保持10厘米以上间距。 | 待复核 |
| holdout-rag-032-1 / answerable | 不同型号的音箱可以按同型号方式组成双机立体声吗？ | m5-candidate-032-c1@m5-candidate-032-v1 | supported；合成蓝牙音箱双机组队需要两台同型号设备。；草拟理由：草拟可允许事实：合成蓝牙音箱双机组队需要两台同型号设备。 | 待复核 |
| holdout-rag-032-2 / answerable | 音箱插上AUX线后蓝牙突然停止播放是否可能正常？ | m5-candidate-032-c1@m5-candidate-032-v1 | supported；合成音箱AUX输入优先于蓝牙，插入音频线后暂停蓝牙播放。；草拟理由：草拟可允许事实：合成音箱AUX输入优先于蓝牙，插入音频线后暂停蓝牙播放。 | 待复核 |
| holdout-rag-032-3 / answerable | 音箱接口潮湿时可以先充电再晾干吗？ | m5-candidate-032-c1@m5-candidate-032-v1 | supported；合成音箱防水不包括充电状态，接口潮湿时禁止充电。；草拟理由：草拟可允许事实：合成音箱防水不包括充电状态，接口潮湿时禁止充电。 | 待复核 |
| holdout-rag-032-4 / answerable | 音箱电量低时最高音量变小是否符合保护策略？ | m5-candidate-032-c1@m5-candidate-032-v1 | supported；合成音箱低电量时会限制最大音量。；草拟理由：草拟可允许事实：合成音箱低电量时会限制最大音量。 | 待复核 |
| holdout-rag-032-5 / answerable | 音箱支持手机通话就代表兼容所有会议软件吗？ | m5-candidate-032-c1@m5-candidate-032-v1 | supported；合成音箱免提通话仅支持手机通话，不保证所有会议APP兼容。；草拟理由：草拟可允许事实：合成音箱免提通话仅支持手机通话，不保证所有会议APP兼容。 | 待复核 |
| holdout-rag-032-6 / answerable | 音箱连接着蓝牙但没有播放会一直保持开机吗？ | m5-candidate-032-c1@m5-candidate-032-v1 | supported；合成音箱自动关机条件为连续15分钟无音频输入。；草拟理由：草拟可允许事实：合成音箱自动关机条件为连续15分钟无音频输入。 | 待复核 |
| holdout-rag-033-1 / answerable | 旧电视只有AV接口能直接连接合成盒子吗？ | m5-candidate-033-c1@m5-candidate-033-v1 | supported；合成盒子仅输出HDMI数字信号，不支持AV模拟输出。；草拟理由：草拟可允许事实：合成盒子仅输出HDMI数字信号，不支持AV模拟输出。 | 待复核 |
| holdout-rag-033-2 / answerable | 盒子支持4K但电视不支持HDCP2.2能保证播放受保护4K内容吗？ | m5-candidate-033-c1@m5-candidate-033-v1 | supported；合成盒子4K播放需要显示设备和线缆均支持HDCP2.2。；草拟理由：草拟可允许事实：合成盒子4K播放需要显示设备和线缆均支持HDCP2.2。 | 待复核 |
| holdout-rag-033-3 / answerable | 盒子恢复出厂会自动取消视频会员扣费吗？ | m5-candidate-033-c1@m5-candidate-033-v1 | supported；合成盒子恢复出厂设置会删除本地应用数据，不取消第三方订阅。；草拟理由：草拟可允许事实：合成盒子恢复出厂设置会删除本地应用数据，不取消第三方订阅。 | 待复核 |
| holdout-rag-033-4 / answerable | 盒子遥控器重新配对应按哪两个键？ | m5-candidate-033-c1@m5-candidate-033-v1 | supported；合成盒子遥控器配对需同时按返回和主页键5秒。；草拟理由：草拟可允许事实：合成盒子遥控器配对需同时按返回和主页键5秒。 | 待复核 |
| holdout-rag-033-5 / answerable | 电视盒子USB口可以给高功率移动硬盘稳定供电吗？ | m5-candidate-033-c1@m5-candidate-033-v1 | supported；合成盒子USB口最大供电5V1A，不建议给大功率硬盘供电。；草拟理由：草拟可允许事实：合成盒子USB口最大供电5V1A，不建议给大功率硬盘供电。 | 待复核 |
| holdout-rag-033-6 / answerable | 盒子插网线后会优先继续使用Wi-Fi吗？ | m5-candidate-033-c1@m5-candidate-033-v1 | supported；合成盒子有线网络与无线网络同时连接时优先有线。；草拟理由：草拟可允许事实：合成盒子有线网络与无线网络同时连接时优先有线。 | 待复核 |
| holdout-rag-034-1 / answerable | 阅读器翻页残影应该先尝试什么刷新方式？ | m5-candidate-034-c1@m5-candidate-034-v1 | supported；合成阅读器电子墨水屏翻页有残影，完整刷新可减少残影。；草拟理由：草拟可允许事实：合成阅读器电子墨水屏翻页有残影，完整刷新可减少残影。 | 待复核 |
| holdout-rag-034-2 / answerable | 带平台加密的电子书可否直接复制到阅读器打开？ | m5-candidate-034-c1@m5-candidate-034-v1 | supported；合成阅读器支持无加密EPUB和PDF，不直接打开加密平台书籍。；草拟理由：草拟可允许事实：合成阅读器支持无加密EPUB和PDF，不直接打开加密平台书籍。 | 待复核 |

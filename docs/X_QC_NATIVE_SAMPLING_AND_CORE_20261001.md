# X 基数据采样链与片段家族引擎接入

## 证据与范围

ZF702 08:08:25 原始文件 SHA e96c5300f5c9b93b5fde3b2c60de5748459873d3a7263d888ac6514b3ae39a48，
旧标准化输入 SHA 3e3b090d9c5ad46b1e6f59c6f52262044f76ed863d85a8fbdfe4ed523ca8a0b4。
原始字段逐值核对排除 REF/SNR/RHO/ZDR/PHI/VEL/SW 的缩放错误。
第9层原始头为 waveform=8、PRF=1200/800 Hz、Nyquist=19.3169727 m/s；
波形枚举语义尚无可靠格式证据，不能由这些参数自动宣告 Doppler 已验证。
新 writer→adapter 全9个REF层真实原文件往返完成：字段、原始码、几何与时间均不变，
新增 native_cut_sampling 保留原始头并绑定 SHA/配置/原生层身份。
新标准化逻辑 SHA a661d478994974d819763d4ddc9ccb479a1ff599b8d2cd14f1fd6672cb5bb7fd。
旧输入仍兼容，无自动 Doppler action 升级，未线上重新解码。

## 已接入的修复

第4层逐门4连通把间歇径向切成短块，完整 RAW 家族内留出源拟合已在54层诊断复核。
现在同一来源引擎按既有 block 开关运行完整 fragment 阶段，位8追踪来源。
共享原资源账本，完整阶段成功才合并；保护、上下文、70%真实配置预算仍生效。
两项引擎接入测试先在旧入口运行失败（缺 fragment_model），修复后通过。
新增资源故障测试证明部分阶段输出不发布。完整 X v2 + decoder + SDK 160 项通过，
另新增资源故障后 fragment_source 10 项通过。

## 下一步正常产品门控

105 fragment-core-fixed-v1 两线程只读运行同六体扫54层，保留旧 task/native/input SHA。
此回放不提供跨层上下文，只用于检查源阶段及原动作门控；不冒充正常产品或界面验收。
须检查每层 status/source resource receipt、新隔离门、原天气/未知门与预算弃权，
再构建仅包含本次 X 文件修改的镜像，经正常 release/task 生命周期重算六例。
原失败、预算弃权凭据保留。第9层非稳定功率残留未解决，不以第4层增益外推。
可信融合、QPE、预报仍不启用，第一优先尚未完成。

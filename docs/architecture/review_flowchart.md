# 旅行险AI自动理赔审核流程

## 航班延误险审核流程

```mermaid
flowchart TD
    Start(["输入: claim_info.json + 材料图片"]) --> Duplicate{"阶段0: 重复理赔检测\n纯代码判定"}

    Duplicate -->|命中| Reject1[["拒赔\n重复申请"]]
    Duplicate -->|未命中| Vision["阶段0-Vision: 视觉/OCR材料抽取\nAI: VISION模型"]

    Vision -->|"成功\n提取航班/证据信息"| Parse["阶段1: 数据解析与时区标准化\nAI: MEDIUM模型"]
    Vision -->|"失败\n降级处理"| Parse

    Parse -->|"成功"| Merge["阶段1.2: 合并Vision结果\n纯代码"]
    Parse -->|"失败"| Manual1[["转人工"]]

    Merge --> Aviation["阶段1.3-1.4: 飞常准API查询\n原航班/替代航班/联程段"]

    Aviation --> Hardcheck["阶段Hardcheck: 代码侧硬校验\n12项确定性检查"]

    Hardcheck --> Exclusion{"阶段2-Precheck: 硬免责前置拦截\n纯代码判定"}

    Exclusion -->|命中| Reject2[["拒赔\n纯代码决定"]]
    Exclusion -->|未命中| AI["阶段2: AI理赔判定\nAI: MEDIUM模型\n综合判断是否赔付"]

    AI -->|"AI输出结果"| Postprocess["后处理: 规则兜底\n纯代码覆盖AI误判"]

    Postprocess --> Output(["输出: audit_result + remark\n通过/拒绝/需补齐资料"])

    %% 硬校验详细内容
    subgraph HardcheckDetail [硬校验12项 · 纯代码 · 100%稳定]
        direction TB
        H1["1. 战争风险检测\n警告不拒赔"] --> H2["2. 承保区域检查"]
        H2 --> H3["3. 保单有效期窗口"]
        H3 --> H4["4. 纯国内航班检测\n是→拒赔"]
        H4 --> H5["5. 境内中转检测\n是→拒赔"]
        H5 --> H6["6. 中转接驳免责\n前序延误→拒赔"]
        H6 --> H7["7. 非客运航班检测"]
        H7 --> H8["8. 同天投保检测\n是→拒赔"]
        H8 --> H9["9. 姓名匹配检测"]
        H9 --> H10["10. 欺诈/既存条件检测"]
        H10 --> H11["11. 必备材料检查"]
        H11 --> H12["12. 可预见因素检测"]
    end

    Hardcheck -.-> HardcheckDetail

    %% 后处理详细内容
    subgraph PostprocessDetail [后处理优先级 · 纯代码兜底]
        direction TB
        P1["优先级1: 硬免责条款\n命中→覆盖为拒绝"] --> P2["优先级2: 延误时长门槛\n<300min→覆盖为拒绝"]
        P2 --> P3["优先级3: 必备材料缺失\n缺材料→覆盖为补件"]
        P3 --> P4["优先级4: AI误判覆盖\n硬校验通过+延误达标\n但AI判拒绝/补件→覆盖为通过"]
    end

    Postprocess -.-> PostprocessDetail
```

## 行李延误险审核流程

```mermaid
flowchart TD
    Start(["输入: claim_info.json + 材料图片"]) --> Duplicate{"阶段0: 重复理赔检测\n纯代码判定"}

    Duplicate -->|命中| Reject1[["拒赔\n重复申请"]]
    Duplicate -->|未命中| Vision["阶段0-Vision: 视觉/OCR材料抽取\nAI: VISION模型"]

    Vision -->|"成功"| Parse["阶段0.5: AI数据解析\nAI: MEDIUM模型"]
    Vision -->|"失败\n降级处理"| Parse

    Parse --> Precheck{"前置准入: 纯代码判定\n保单有效期/身份/纯国内/除外责任"}

    Precheck -->|命中拒赔条件| Reject2[["拒赔\n纯代码决定"]]
    Precheck -->|通过| Aviation["阶段1: 飞常准API查询\n航班到达时间"]

    Aviation --> Transfer["阶段2: 转运航班到达时间回退\n纯代码"]

    Transfer --> PolicyCheck["阶段3: 实际到达vs保单有效期\n纯代码"]

    PolicyCheck --> AccidentType{"阶段4: 事故类型校验\n丢失 vs 延误\nAI+代码"}

    AccidentType -->|"行李丢失"| Reject3[["拒赔\n转随身财产损失"]]
    AccidentType -->|"行李延误"| MaterialGate["阶段5: 材料门禁\n纯代码+规则库"]

    MaterialGate -->|"材料不全"| Supplement[["需补齐资料"]]
    MaterialGate -->|"材料齐全"| AI["阶段6: AI审核+赔付核算\nAI: MEDIUM模型"]

    AI --> Calc["赔付金额计算\n纯代码: 6h→500, 12h→1000, 18h→1500"]

    Calc --> Output(["输出: audit_result + remark\n通过/拒绝/需补齐资料"])
```

## AI不稳定性工程防御体系

```mermaid
flowchart TB
    subgraph L1 ["第一层: 可靠性保障"]
        R1["StageRunner重试\n最多3次, 指数退避"] --> R2["熔断器\n连续5次失败→熔断30s"]
    end

    subgraph L2 ["第二层: JSON解析保障"]
        J1["json_repair修复"] --> J2["json.loads直接解析"]
        J2 --> J3["正则提取{...}"]
        J3 --> J4["手动修复转义符"]
        J4 --> J5["截断修复补}"]
    end

    subgraph L3 ["第三层: 代码前置拦截"]
        C1["硬免责命中→不调AI"] --> C2["硬校验结果注入parsed"]
        C2 --> C3["AI只能参考代码判定"]
    end

    subgraph L4 ["第四层: 后处理兜底 (最重要)"]
        P1["硬免责→覆盖为拒绝"] --> P2["延误<门槛→覆盖为拒绝"]
        P2 --> P3["缺材料→覆盖为补件"]
        P3 --> P4["AI误判→覆盖为通过"]
    end

    Input[AI输出] --> L1
    L1 --> L2
    L2 --> L3
    L3 --> L4
    L4 --> Output[确定性结果]
```

## AI与代码分工表

| 任务 | 谁做 | 稳定性 | 说明 |
|------|:---:|:---:|------|
| 重复理赔检测 | 代码 | 100% | 身份证号+产品+险种+事故日+航班号匹配 |
| 视觉/OCR材料抽取 | AI | 不稳定 | 图片理解是AI强项 |
| 数据解析提取 | AI | 不稳定 | 非结构化文本需要AI理解 |
| 时区标准化 | AI | 不稳定 | 需要理解时间上下文 |
| 飞常准数据查询 | API | 100% | 权威数据源 |
| **硬免责检查** | **代码** | **100%** | 保单有效期/国内航班/中转/同天投保等 |
| 延误时长计算 | 代码 | 100% | 数学计算 |
| 材料完整性检查 | 代码 | 100% | 规则明确 |
| 理赔综合判定 | AI→代码兜底 | 不确定→确定 | AI先判，代码后覆盖 |
| 赔付金额计算 | 代码 | 100% | 档位查询 |
| **最终结果覆盖** | **代码** | **100%** | **确保结果确定性** |

## 核心原则

> **AI负责"提取和理解"，代码负责"判定和兜底"。**
>
> 所有关键判定（免责、门槛、材料）均在代码中完成，AI的输出会被代码验证、修正、覆盖。
> 系统设计保证：即使AI输出完全错误，最终结果仍由确定性规则控制。

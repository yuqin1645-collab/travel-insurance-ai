# AI理赔审核系统

基于大模型的智能理赔审核系统,支持OCR识别、隐私脱敏、自动审核和质量评估。

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置
复制 `.env.example` 到 `.env` 并填写配置:
```bash
cp .env.example .env
```

编辑 `.env` 文件,填写API密钥:
```env
OPENROUTER_API_KEY=your_api_key_here
OCR_PROVIDER=tesseract
TESSERACT_PATH=D:\app\tools\other\Tesseract\tesseract.exe
```

### 3. 运行
```bash
# 下载案件数据
python scripts/download_claims.py

# 运行AI审核
python main.py
```

## 项目结构

```
├── app/                    # 核心应用代码
├── scripts/                # 工具脚本
├── docs/                   # 文档
├── static/                 # 静态资源(条款、清单)
├── prompts/                # Prompt模板
├── claims_data/            # 案件数据
├── review_results/         # 审核结果
├── logs/                   # 日志
└── main.py                 # 主入口
```

详细说明见 `docs/项目结构说明.md`

## 功能特性

- ✅ **异步并发处理** - 大幅提升处理速度(350倍+)
- ✅ OCR识别(支持Tesseract本地OCR)
- ✅ 隐私脱敏(身份证、手机号、银行卡等)
- ✅ AI分阶段审核(5个审核阶段)
- ✅ 批量审核进度显示
- ✅ 审核质量评估

### 性能

- **525个案件**: 从4.4小时 → 45秒
- **平均速度**: 0.09秒/案件
- **并发执行**: 多个案件同时处理

## 技术栈

- Python 3.14+
- OpenRouter API (Claude模型)
- Tesseract OCR
- pytesseract, Pillow

## 文档

- [项目结构说明](docs/项目结构说明.md)
- [异步并发说明](docs/异步并发说明.md)
- [快速开始](docs/快速开始.md)
- [API返回格式说明](docs/API返回格式说明.md)

## License

MIT

## force下载接口的返回字段
- forceid：系统唯一 ID
- ClaimId：理赔单号
- PolicyNo：保单号
- Effective_Date：生效日
- Expiry_Date：到期日
- Date_of_Terminated：终止日
- BenefitName：保障责任
- Product_Name：产品名
- Plan_Name：计划名
- Birthday：生日
- Age：年龄
- Gender：性别
- ID_Number：证件号
- ID_Type：证件类型
- Relationship_with_Insured：与被保人关系
- Insurance_Company：保险公司
- Applicant_Name：申请人
- Date_of_Accident：事故日期
- Description_of_Accident：事故说明
- Insured_Amount：总保额
- Amount：理赔金额
- Remaining_Coverage：剩余保额
- Reserved_Amount：预留金额
- Insured_And_Policy：被保人 + 保单
- FileList：附件列表
- SamePolicyClaim：同保单其他理赔
- FileUrl：文件地址

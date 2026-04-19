-- 案件原始下载信息存档表
-- 用途：将 claim_info.json 的关键字段落库，数据丢失时可追溯
-- 版本: 004

CREATE TABLE IF NOT EXISTS ai_claim_info_raw (
    id                      BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    -- 唯一标识
    forceid                 VARCHAR(64)  NOT NULL COMMENT '案件唯一ID (Force平台)',
    claim_id                VARCHAR(64)  NULL     COMMENT '理赔单号 (ClaimId)',

    -- 权益 / 受益人维度
    benefit_name            VARCHAR(64)  NULL     COMMENT '险种名称 (BenefitName)',
    applicant_name          VARCHAR(128) NULL     COMMENT '申请人姓名 (Applicant_Name)',

    -- 被保险人信息（来自 samePolicyClaim）
    insured_name            VARCHAR(128) NULL     COMMENT '被保险人姓名 (Insured_And_Policy)',
    id_type                 VARCHAR(32)  NULL     COMMENT '证件类型 (ID_Type)',
    id_number               VARCHAR(64)  NULL     COMMENT '证件号码 (ID_Number)',
    birthday                DATE         NULL     COMMENT '出生日期 (Birthday)',
    gender                  VARCHAR(8)   NULL     COMMENT '性别 (Gender)',

    -- 保单信息
    policy_no               VARCHAR(64)  NULL     COMMENT '保单号 (PolicyNo)',
    insurance_company       VARCHAR(128) NULL     COMMENT '保险公司 (Insurance_Company)',
    product_name            VARCHAR(128) NULL     COMMENT '产品名称 (Product_Name)',
    plan_name               VARCHAR(128) NULL     COMMENT '计划名称 (Plan_Name)',
    effective_date          VARCHAR(32)  NULL     COMMENT '保单生效日期 (Effective_Date)',
    expiry_date             VARCHAR(32)  NULL     COMMENT '保单到期日期 (Expiry_Date)',
    date_of_insurance       VARCHAR(32)  NULL     COMMENT '投保日期 (Date_of_Insurance)',

    -- 案件信息（本案维度，camelCase字段）
    case_insured_name       VARCHAR(128) NULL     COMMENT '本案被保险人姓名 (insured_And_Policy)',
    case_policy_no          VARCHAR(64)  NULL     COMMENT '本案保单号 (policyNo)',
    case_insurance_company  VARCHAR(128) NULL     COMMENT '本案保险公司 (insurance_Company)',
    case_effective_date     VARCHAR(32)  NULL     COMMENT '本案保单生效 (effective_Date)',
    case_expiry_date        VARCHAR(32)  NULL     COMMENT '本案保单到期 (expiry_Date)',
    case_id_type            VARCHAR(32)  NULL     COMMENT '本案证件类型 (iD_Type)',
    case_id_number          VARCHAR(64)  NULL     COMMENT '本案证件号码 (iD_Number)',
    insured_amount          DECIMAL(10,2) NULL    COMMENT '保额 (insured_Amount / Insured_Amount)',
    reserved_amount         DECIMAL(10,2) NULL    COMMENT '核定金额 (reserved_Amount / Reserved_Amount)',
    remaining_coverage      DECIMAL(10,2) NULL    COMMENT '剩余保额 (remaining_Coverage / Remaining_Coverage)',
    claim_amount            DECIMAL(10,2) NULL    COMMENT '申请金额 (amount / Amount)',

    -- 事故信息
    date_of_accident        DATE         NULL     COMMENT '事故日期 (date_of_Accident / Date_of_Accident)',
    final_status            VARCHAR(64)  NULL     COMMENT '案件状态 (final_Status / Final_Status)',
    description_of_accident TEXT         NULL     COMMENT '事故经过描述',

    -- 渠道信息
    source_date             VARCHAR(128) NULL     COMMENT '来源渠道 (Source_Date)',

    -- 原始 JSON（完整备份，用于追溯任何字段）
    raw_json                LONGTEXT     NULL     COMMENT '完整 claim_info.json 原始内容',

    -- 时间戳
    downloaded_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次下载写入时间',
    updated_at              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_forceid (forceid),
    KEY idx_claim_id (claim_id),
    KEY idx_policy_no (policy_no),
    KEY idx_insured_name (insured_name),
    KEY idx_benefit_name (benefit_name),
    KEY idx_final_status (final_status),
    KEY idx_date_of_accident (date_of_accident)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='案件原始下载信息存档（claim_info.json 落库备份）';

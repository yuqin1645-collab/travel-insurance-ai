# 航班延误AI审核系统 - 生产化部署指南

## 系统架构

本系统已完成生产化升级，具备以下核心能力：

### 1. 定时增量下载
- 每小时自动检查新案件
- 增量下载，避免重复处理
- 支持断点续传

### 2. 状态管理
- 完整的案件生命周期状态机
- 数据库持久化存储
- 状态变更历史记录

### 3. 补件处理
- 自动检测"需补件"结果
- 24小时补件截止时间
- 最多3次补件机会
- 超时自动拒绝

### 4. 双路输出
- 同时推送到前端API
- 同时写入MySQL数据库
- 确保数据一致性

### 5. 监控告警
- 关键指标监控
- 多渠道告警（邮件、Slack、短信）
- 健康检查端点

## 部署步骤

### 第一步：数据库配置

1. 创建MySQL数据库：
```sql
CREATE DATABASE ai_claim_review CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

2. 运行数据库迁移：
```bash
python scripts/db/run_migration.py
```

### 第二步：环境配置

1. 复制生产环境配置：
```bash
cp .env.production .env
```

2. 修改 `.env` 文件，配置：
   - OpenRouter API密钥
   - 数据库连接信息
   - 前端API地址
   - 告警通知配置

### 第三步：安装依赖

```bash
pip install -r requirements.txt
```

### 第四步：启动系统

```bash
python start_production.py
```

系统将自动启动定时任务调度器，开始处理案件。

## 核心组件

### 1. 定时任务调度器
- 位置：`app/scheduler/task_scheduler.py`
- 功能：协调所有定时任务
- 任务：
  - 每小时检查新案件
  - 每10分钟审核待处理案件
  - 每30分钟检查补件状态
  - 每天凌晨2点清理数据

### 2. 增量下载调度器
- 位置：`app/scheduler/download_scheduler.py`
- 功能：增量下载新案件
- 特点：
  - 基于最后下载时间增量查询
  - 避免重复下载已处理案件
  - 支持失败重试

### 3. 审核调度器
- 位置：`app/scheduler/review_scheduler.py`
- 功能：自动审核待处理案件
- 特点：
  - 批量处理，每批3个案件
  - 支持失败重试

### 4. 补件处理器
- 位置：`app/supplementary/handler.py`
- 功能：处理补件流程
- 特点：
  - 自动创建补件记录
  - 监控补件截止时间
  - 发送补件提醒
  - 处理补件超时

### 5. 输出协调器
- 位置：`app/output/coordinator.py`
- 功能：双路输出审核结果
- 特点：
  - 同时推送到前端和数据库
  - 支持失败重试
  - 保证数据一致性

### 6. 状态管理器
- 位置：`app/state/status_manager.py`
- 功能：管理案件状态
- 特点：
  - 状态机驱动
  - 数据库持久化
  - 状态变更历史

### 7. 告警管理器
- 位置：`app/monitoring/alert_manager.py`
- 功能：监控和告警
- 特点：
  - 关键指标监控
  - 多渠道告警
  - 告警历史记录

### 8. 健康检查器
- 位置：`app/monitoring/health_check.py`
- 功能：系统健康检查
- 检查项：
  - 数据库连接
  - API连通性
  - 磁盘空间
  - 内存使用
  - 任务执行情况

## 数据库表结构

### ai_claim_status
案件状态管理表，记录案件全生命周期状态。

### ai_review_result
审核结果表，存储AI审核结论。

### ai_supplementary_records
补件记录表，记录补件请求和处理情况。

### ai_scheduler_logs
定时任务日志表，记录任务执行历史。

### ai_status_history
状态变更历史表，记录状态变更记录。

## 状态流转

### 下载状态
```
pending → downloading → downloaded → failed
                     ↘ retrying
```

### 审核状态
```
pending → reviewing →
         ├─→ completed (审核完成)
         ├─→ supplementary_needed (需补件)
         └─→ failed (审核失败)
```

### 补件状态
```
requested → received → verified
         ↘ timeout
         ↘ rejected
```

## 监控指标

### 关键指标
1. **下载失败率**：阈值 20%（1小时窗口）
2. **审核失败率**：阈值 10%（1小时窗口）
3. **审核延迟**：阈值 300秒（10分钟窗口）
4. **补件超时数**：阈值 10个（1小时窗口）
5. **系统错误数**：阈值 1个（5分钟窗口）

### 查看系统状态
```bash
python -m app.production.main_workflow --mode status
```

## 手动操作

### 运行单次检查
```bash
python -m app.production.main_workflow --mode hourly
```

### 处理单个案件
```bash
python -m app.production.main_workflow --mode single --forceid <案件ID>
```

### 运行清理任务
```bash
python -m app.production.main_workflow --mode cleanup --days 30
```

## 故障排查

### 查看日志
```bash
tail -f logs/production.log
```

### 检查数据库连接
```bash
python scripts/db/run_migration.py --check-only
```

### 查看任务执行情况
```bash
python -m app.production.main_workflow --mode status
```

## 配置说明

### 定时间隔
- `DOWNLOAD_INTERVAL`：下载间隔（秒），默认3600（1小时）
- `REVIEW_INTERVAL`：审核间隔（秒），默认600（10分钟）
- `SUPPLEMENTARY_CHECK_INTERVAL`：补件检查间隔（秒），默认1800（30分钟）

### 重试配置
- `MAX_DOWNLOAD_RETRIES`：下载最大重试次数，默认3
- `MAX_REVIEW_RETRIES`：审核最大重试次数，默认2
- `RETRY_BACKOFF_BASE`：重试退避基数，默认2

### 补件配置
- `SUPPLEMENTARY_DEADLINE_HOURS`：补件截止时间（小时），默认24
- `MAX_SUPPLEMENTARY_COUNT`：最大补件次数，默认3
- `SUPPLEMENTARY_REMINDER_HOURS`：补件提醒时间（小时前），默认6

## 扩展开发

### 添加新的案件类型
1. 在 `app/modules/` 下创建新模块
2. 在 `app/modules/registry.py` 中注册
3. 系统会自动支持新案件类型

### 添加新的告警规则
1. 在 `app/monitoring/alert_manager.py` 中添加规则
2. 实现 `_get_metric()` 方法
3. 配置告警渠道

### 添加新的健康检查项
1. 在 `app/monitoring/health_check.py` 中添加检查方法
2. 在 `check_health()` 中调用

## 注意事项

1. **数据库连接**：确保数据库连接池配置合理
2. **并发控制**：系统已限制最大并发数，避免过载
3. **错误处理**：所有关键操作都有错误处理和重试机制
4. **日志记录**：关键操作都有详细日志记录
5. **监控告警**：配置告警通知，及时发现问题

## 技术支持

如有问题，请联系开发团队或查看系统日志进行排查。
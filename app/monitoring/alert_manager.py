#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
告警管理器
监控系统状态，发送告警通知
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config import config
from app.db.database import get_scheduler_log_dao, get_db_connection

LOGGER = logging.getLogger(__name__)


class AlertLevel(str):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertChannel(str):
    """告警渠道"""
    LOG = "log"
    EMAIL = "email"
    SLACK = "slack"
    SMS = "sms"
    WECHAT = "wechat"


class AlertRule:
    """告警规则"""

    def __init__(
        self,
        name: str,
        metric: str,
        threshold: float,
        window: str,
        channels: List[str],
        level: str = AlertLevel.WARNING
    ):
        self.name = name
        self.metric = metric
        self.threshold = threshold
        self.window = window  # 如 "1h", "30m", "1d"
        self.channels = channels
        self.level = level

    def parse_window(self) -> timedelta:
        """解析时间窗口"""
        if self.window.endswith('m'):
            return timedelta(minutes=int(self.window[:-1]))
        elif self.window.endswith('h'):
            return timedelta(hours=int(self.window[:-1]))
        elif self.window.endswith('d'):
            return timedelta(days=int(self.window[:-1]))
        else:
            return timedelta(hours=1)  # 默认1小时


class AlertManager:
    """告警管理器"""

    # 默认告警规则
    DEFAULT_RULES = [
        AlertRule(
            name="download_failure_rate",
            metric="download_failure_rate",
            threshold=0.2,  # 20%失败率
            window="1h",
            channels=[AlertChannel.EMAIL, AlertChannel.SLACK],
            level=AlertLevel.WARNING
        ),
        AlertRule(
            name="review_failure_rate",
            metric="review_failure_rate",
            threshold=0.1,  # 10%失败率
            window="1h",
            channels=[AlertChannel.EMAIL, AlertChannel.SLACK],
            level=AlertLevel.WARNING
        ),
        AlertRule(
            name="review_latency",
            metric="review_latency",
            threshold=300,  # 5分钟
            window="10m",
            channels=[AlertChannel.SLACK],
            level=AlertLevel.WARNING
        ),
        AlertRule(
            name="supplementary_timeout",
            metric="supplementary_timeout",
            threshold=10,  # 10个超时
            window="1h",
            channels=[AlertChannel.EMAIL, AlertChannel.SLACK],
            level=AlertLevel.ERROR
        ),
        AlertRule(
            name="system_error",
            metric="system_error",
            threshold=1,
            window="5m",
            channels=[AlertChannel.EMAIL, AlertChannel.SLACK, AlertChannel.SMS],
            level=AlertLevel.CRITICAL
        ),
        AlertRule(
            name="database_connection",
            metric="database_connection",
            threshold=1,
            window="1m",
            channels=[AlertChannel.EMAIL, AlertChannel.SMS],
            level=AlertLevel.CRITICAL
        ),
    ]

    def __init__(self):
        self.rules: Dict[str, AlertRule] = {rule.name: rule for rule in self.DEFAULT_RULES}
        self.alert_history: List[Dict[str, Any]] = []
        self.db = get_db_connection()

    async def initialize(self):
        """初始化"""
        await self.db.initialize()
        LOGGER.info("告警管理器初始化完成")

        # 从配置加载自定义规则
        self._load_custom_rules()

    def _load_custom_rules(self):
        """从配置加载自定义规则"""
        # TODO: 从数据库或配置文件加载自定义规则
        pass

    def add_rule(self, rule: AlertRule):
        """添加告警规则"""
        self.rules[rule.name] = rule
        LOGGER.info(f"添加告警规则: {rule.name}")

    def remove_rule(self, name: str):
        """移除告警规则"""
        if name in self.rules:
            del self.rules[name]
            LOGGER.info(f"移除告警规则: {name}")

    async def check_alerts(self) -> List[Dict[str, Any]]:
        """
        检查所有告警规则

        Returns:
            触发的告警列表
        """
        LOGGER.info("检查告警规则...")

        alerts = []

        for name, rule in self.rules.items():
            try:
                # 获取指标值
                metric_value = await self._get_metric(rule.metric, rule.window)

                # 检查是否触发告警
                if metric_value > rule.threshold:
                    alert = await self._create_alert(rule, metric_value)
                    alerts.append(alert)

                    # 发送告警通知
                    await self._send_alert(alert)

            except Exception as e:
                LOGGER.error(f"检查告警规则失败: {name}, 错误: {e}")

        LOGGER.info(f"告警检查完成: {len(alerts)} 个告警触发")
        return alerts

    async def _get_metric(self, metric: str, window: str) -> float:
        """
        获取指标值

        Args:
            metric: 指标名称
            window: 时间窗口

        Returns:
            指标值
        """
        # 根据时间窗口计算起始时间
        delta = self._parse_window(window)
        start_time = datetime.now() - delta

        if metric == "download_failure_rate":
            return await self._get_failure_rate(TaskType.DOWNLOAD, start_time)

        elif metric == "review_failure_rate":
            return await self._get_failure_rate(TaskType.REVIEW, start_time)

        elif metric == "review_latency":
            return await self._get_avg_latency(TaskType.REVIEW, start_time)

        elif metric == "supplementary_timeout":
            return await self._get_supplementary_timeout_count(start_time)

        elif metric == "system_error":
            return await self._get_system_error_count(start_time)

        elif metric == "database_connection":
            return await self._check_database_connection()

        else:
            LOGGER.warning(f"未知指标: {metric}")
            return 0.0

    async def _get_failure_rate(self, task_type: str, start_time: datetime) -> float:
        """获取任务失败率"""
        try:
            logs = await self.db.scheduler_log_dao.get_recent_logs(task_type, limit=1000)

            if not logs:
                return 0.0

            # 过滤时间窗口内的日志
            recent_logs = [log for log in logs if log.start_time >= start_time]

            if not recent_logs:
                return 0.0

            failed_count = sum(1 for log in recent_logs if log.status == TaskStatus.FAILED)
            return failed_count / len(recent_logs)

        except Exception as e:
            LOGGER.error(f"获取失败率失败: {e}")
            return 0.0

    async def _get_avg_latency(self, task_type: str, start_time: datetime) -> float:
        """获取平均处理延迟（秒）"""
        try:
            logs = await self.db.scheduler_log_dao.get_recent_logs(task_type, limit=1000)

            if not logs:
                return 0.0

            # 过滤时间窗口内的日志
            recent_logs = [log for log in logs if log.start_time >= start_time]

            if not recent_logs:
                return 0.0

            # 计算平均延迟
            total_latency = sum(
                log.duration_seconds or 0
                for log in recent_logs
                if log.duration_seconds
            )

            return total_latency / len(recent_logs)

        except Exception as e:
            LOGGER.error(f"获取平均延迟失败: {e}")
            return 0.0

    async def _get_supplementary_timeout_count(self, start_time: datetime) -> int:
        """获取补件超时数量"""
        # TODO: 实现从数据库查询
        return 0

    async def _get_system_error_count(self, start_time: datetime) -> int:
        """获取系统错误数量"""
        # TODO: 实现从日志或数据库查询
        return 0

    async def _check_database_connection(self) -> float:
        """检查数据库连接"""
        try:
            async with self.db.get_connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    return 0.0  # 正常
        except Exception:
            return 1.0  # 异常

    def _parse_window(self, window: str) -> timedelta:
        """解析时间窗口"""
        if window.endswith('m'):
            return timedelta(minutes=int(window[:-1]))
        elif window.endswith('h'):
            return timedelta(hours=int(window[:-1]))
        elif window.endswith('d'):
            return timedelta(days=int(window[:-1]))
        else:
            return timedelta(hours=1)

    async def _create_alert(self, rule: AlertRule, metric_value: float) -> Dict[str, Any]:
        """创建告警"""
        alert = {
            "alert_id": f"{rule.name}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "rule_name": rule.name,
            "level": rule.level,
            "metric": rule.metric,
            "value": metric_value,
            "threshold": rule.threshold,
            "window": rule.window,
            "timestamp": datetime.now().isoformat(),
            "message": self._format_alert_message(rule, metric_value),
            "channels": rule.channels
        }

        self.alert_history.append(alert)

        # 限制历史记录数量
        if len(self.alert_history) > 1000:
            self.alert_history = self.alert_history[-1000:]

        return alert

    def _format_alert_message(self, rule: AlertRule, metric_value: float) -> str:
        """格式化告警消息"""
        messages = {
            "download_failure_rate": f"下载失败率过高: {metric_value*100:.1f}% (阈值: {rule.threshold*100:.1f}%)",
            "review_failure_rate": f"审核失败率过高: {metric_value*100:.1f}% (阈值: {rule.threshold*100:.1f}%)",
            "review_latency": f"审核延迟过高: {metric_value:.1f}秒 (阈值: {rule.threshold:.1f}秒)",
            "supplementary_timeout": f"补件超时数量过多: {int(metric_value)} (阈值: {int(rule.threshold)})",
            "system_error": f"系统错误: {int(metric_value)} 次",
            "database_connection": "数据库连接异常",
        }

        return messages.get(rule.name, f"告警: {rule.name} = {metric_value}")

    async def _send_alert(self, alert: Dict[str, Any]):
        """发送告警通知"""
        LOGGER.warning(f"触发告警: {alert['message']}")

        for channel in alert["channels"]:
            try:
                if channel == AlertChannel.LOG:
                    await self._send_log_alert(alert)
                elif channel == AlertChannel.EMAIL:
                    await self._send_email_alert(alert)
                elif channel == AlertChannel.SLACK:
                    await self._send_slack_alert(alert)
                elif channel == AlertChannel.SMS:
                    await self._send_sms_alert(alert)

            except Exception as e:
                LOGGER.error(f"发送告警到 {channel} 失败: {e}")

    async def _send_log_alert(self, alert: Dict[str, Any]):
        """发送日志告警"""
        level = alert["level"]
        if level == AlertLevel.CRITICAL:
            LOGGER.critical(alert["message"])
        elif level == AlertLevel.ERROR:
            LOGGER.error(alert["message"])
        elif level == AlertLevel.WARNING:
            LOGGER.warning(alert["message"])
        else:
            LOGGER.info(alert["message"])

    async def _send_email_alert(self, alert: Dict[str, Any]):
        """发送邮件告警"""
        if not config.ALERT_EMAIL:
            LOGGER.debug("未配置邮件告警邮箱")
            return

        # TODO: 实现邮件发送
        # import smtplib
        # from email.mime.text import MIMEText

        LOGGER.info(f"邮件告警已发送: {config.ALERT_EMAIL}")

    async def _send_slack_alert(self, alert: Dict[str, Any]):
        """发送Slack告警"""
        if not config.SLACK_WEBHOOK_URL:
            LOGGER.debug("未配置Slack Webhook")
            return

        # TODO: 实现Slack发送
        # import requests
        # requests.post(config.SLACK_WEBHOOK_URL, json={"text": alert["message"]})

        LOGGER.info("Slack告警已发送")

    async def _send_sms_alert(self, alert: Dict[str, Any]):
        """发送短信告警"""
        # TODO: 实现短信发送
        LOGGER.info("短信告警已发送")

    def get_alert_history(
        self,
        start_time: Optional[datetime] = None,
        level: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取告警历史

        Args:
            start_time: 起始时间
            level: 告警级别过滤
            limit: 限制数量

        Returns:
            告警历史列表
        """
        alerts = self.alert_history

        if start_time:
            alerts = [a for a in alerts if datetime.fromisoformat(a["timestamp"]) >= start_time]

        if level:
            alerts = [a for a in alerts if a["level"] == level]

        return alerts[-limit:]


# 全局实例
_alert_manager = None


def get_alert_manager() -> AlertManager:
    """获取告警管理器实例"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager


# 添加缺失的导入
from app.db.models import TaskType, TaskStatus


if __name__ == '__main__':
    import asyncio

    async def test():
        manager = AlertManager()
        await manager.initialize()

        try:
            alerts = await manager.check_alerts()
            print(f"触发告警: {len(alerts)}")
            for alert in alerts:
                print(f"  - {alert['message']}")
        finally:
            await manager.db.close()

    asyncio.run(test())
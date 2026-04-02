#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量下载调度器
每小时检查新案件并下载
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path

import aiohttp
import requests

from app.config import config
from app.state.status_manager import get_status_manager, StatusManager
from app.state.constants import ClaimStatus, DownloadStatus
from app.db.models import ClaimStatusRecord, SchedulerLog, TaskType, TaskStatus
from app.db.database import get_scheduler_log_dao, get_db_connection

# 已结案状态，直接跳过不处理
CONCLUDED_STATUSES = {
    "零结关案",
    "支付成功",
    "事后理赔拒赔",
    "取消理赔",
    "结案待财务付款",
}

LOGGER = logging.getLogger(__name__)


class IncrementalDownloadScheduler:
    """增量下载调度器"""

    def __init__(
        self,
        status_manager: Optional[StatusManager] = None,
        api_url: Optional[str] = None,
        output_dir: Optional[Path] = None
    ):
        self.status_manager = status_manager or get_status_manager()
        self.api_url = api_url or os.getenv('CLAIMS_API_URL', '')
        self.output_dir = output_dir or config.CLAIMS_DATA_DIR
        self.db = get_db_connection()
        self.scheduler_log_dao = get_scheduler_log_dao()

        # 进度文件
        self.progress_file = self.output_dir / '.download_progress.json'
        self._progress_cache: Dict[str, Any] = {}

    async def initialize(self):
        """初始化"""
        await self.db.initialize()
        self._load_progress()
        LOGGER.info("增量下载调度器初始化完成")

    def _load_progress(self):
        """加载下载进度"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    self._progress_cache = json.load(f)
                LOGGER.info(f"加载下载进度: {len(self._progress_cache)} 个案件")
            except Exception as e:
                LOGGER.error(f"加载下载进度失败: {e}")
                self._progress_cache = {}
        else:
            self._progress_cache = {}

    def _save_progress(self):
        """保存下载进度"""
        try:
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump(self._progress_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            LOGGER.error(f"保存下载进度失败: {e}")

    async def run_hourly_check(self) -> Tuple[int, str]:
        """
        每小时检查新案件 - 使用 ClaimDownloader 从 API 拉取并下载

        Returns:
            (新下载案件数, 消息)
        """
        LOGGER.info("开始每小时案件检查...")

        if not self.api_url:
            LOGGER.warning("未配置API URL（CLAIMS_API_URL），跳过下载")
            return 0, "未配置API URL"

        log = SchedulerLog(
            task_type=TaskType.DOWNLOAD,
            start_time=datetime.now(),
            status=TaskStatus.RUNNING
        )
        log_id = await self.scheduler_log_dao.create_log(log)

        try:
            # 用 ClaimDownloader 拉取并下载（在线程池里跑同步代码）
            from scripts.download_claims import ClaimDownloader
            downloader = ClaimDownloader(
                api_url=self.api_url,
                output_dir=str(self.output_dir),
                force_refresh=False,
            )

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, downloader.run, {})

            # 把下载完成的案件注册到状态管理器（供 review_scheduler 发现）
            new_count = 0
            for case_no, record in downloader.progress.items():
                if record.get("status") not in ("completed", "partial"):
                    continue
                forceid = record.get("forceid") or record.get("Id") or ""
                if not forceid:
                    # 从 claim_info.json 读取 forceid
                    benefit_name = record.get("benefitName", "")
                    case_dir = self.output_dir / benefit_name / f"{benefit_name}-案件号【{case_no}】"
                    info_file = case_dir / "claim_info.json"
                    if info_file.exists():
                        try:
                            info = json.loads(info_file.read_text(encoding="utf-8"))
                            forceid = info.get("forceid", "")
                        except Exception:
                            pass
                if not forceid:
                    continue
                # 检查状态管理器里是否已有记录，避免重复注册
                existing = await self.status_manager.get_claim_status(forceid)
                if existing is None:
                    benefit_name = record.get("benefitName", "")
                    claim_type = "flight_delay" if "延误" in benefit_name else "baggage_damage"
                    await self.status_manager.create_claim_status(
                        claim_id=case_no,
                        forceid=forceid,
                        claim_type=claim_type,
                        initial_status=ClaimStatus.DOWNLOADED,
                    )
                    LOGGER.info(f"注册新案件到审核队列: {forceid} ({claim_type})")
                    new_count += 1

            await self.scheduler_log_dao.update_log(
                log_id, TaskStatus.SUCCESS, new_count, new_count, 0, None
            )
            message = f"下载完成，共处理 {new_count} 个案件"
            LOGGER.info(message)
            return new_count, message

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"下载任务异常: {error_msg}")
            await self.scheduler_log_dao.update_log(
                log_id, TaskStatus.FAILED, 0, 0, 0, error_msg
            )
            return 0, f"任务异常: {error_msg}"

    async def _fetch_new_claims(self) -> List[Dict[str, Any]]:
        """
        从API获取新案件

        Returns:
            案件列表
        """
        # 获取最后下载时间
        last_download_time = self._get_last_download_time()

        LOGGER.info(f"查询新案件: 起始时间={last_download_time}")

        try:
            # 构建查询参数
            payload = {
                "startTime": last_download_time.isoformat() if last_download_time else None,
                "pageSize": 100,
                "includeUpdated": True
            }

            # 调用API
            response = await self._call_api(payload)

            if response and 'claims' in response:
                claims = response['claims']
                LOGGER.info(f"API返回 {len(claims)} 个案件")
                return claims

            return []

        except Exception as e:
            LOGGER.error(f"获取新案件失败: {e}")
            return []

    async def _call_api(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        调用案件API

        Args:
            payload: 请求参数

        Returns:
            API响应
        """
        if not self.api_url:
            LOGGER.warning("未配置API URL，跳过API调用")
            return None

        try:
            # 使用同步requests进行API调用（可根据实际情况改为aiohttp）
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=config.TIMEOUT or 30,
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
            )

            if response.status_code == 200:
                return response.json()
            else:
                LOGGER.error(f"API调用失败: {response.status_code} - {response.text}")
                return None

        except requests.exceptions.Timeout:
            LOGGER.error("API调用超时")
            return None
        except requests.exceptions.RequestException as e:
            LOGGER.error(f"API调用异常: {e}")
            return None

    def _filter_unprocessed(self, claims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        过滤出未处理的案件

        优先级：
        1. Final_Status 在 CONCLUDED_STATUSES 中 → 直接跳过（已结案）
        2. 状态机中已有最终状态（approved/rejected/completed）且 Final_Status 未变化 → 跳过（已完成）
        3. 其余情况（包括补件后重新提交）→ 需要处理

        Args:
            claims: 案件列表

        Returns:
            未处理的案件列表
        """
        unprocessed = []

        # 从状态管理器获取所有已知forceid的状态（同步方式查缓存）
        final_statuses = {
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
            ClaimStatus.COMPLETED,
            ClaimStatus.MAX_RETRIES_EXCEEDED,
        }

        for claim in claims:
            forceid = claim.get('forceid') or claim.get('Id', '')

            # 1. 优先检查 Final_Status（已结案直接跳过）
            final_status = str(claim.get('Final_Status') or '').strip()
            if final_status in CONCLUDED_STATUSES:
                LOGGER.debug(f"跳过已结案案件: {forceid}, Final_Status={final_status}")
                continue

            # 2. 检查状态机中是否已有最终状态
            # 使用进度缓存作为快速查找（已处理过的forceid都记录在此）
            if forceid in self._progress_cache:
                cached = self._progress_cache[forceid]
                cached_status = cached.get('claim_status', '')
                if cached_status in [s.value for s in final_statuses]:
                    LOGGER.debug(f"跳过已完成案件: {forceid}, 状态={cached_status}")
                    continue

            unprocessed.append(claim)

        return unprocessed

    def _get_last_download_time(self) -> Optional[datetime]:
        """
        获取最后成功下载时间

        Returns:
            最后下载时间
        """
        last_time = None

        for forceid, progress in self._progress_cache.items():
            if progress.get('status') == 'completed':
                completed_time_str = progress.get('completed_at')
                if completed_time_str:
                    try:
                        completed_time = datetime.fromisoformat(completed_time_str)
                        if last_time is None or completed_time > last_time:
                            last_time = completed_time
                    except Exception:
                        continue

        # 如果没有记录，返回24小时前
        if last_time is None:
            return datetime.now() - timedelta(hours=24)

        return last_time

    def _determine_claim_type(self, claim: Dict[str, Any]) -> str:
        """
        判断案件类型

        Args:
            claim: 案件信息

        Returns:
            案件类型
        """
        benefit_name = claim.get('BenefitName', '') or claim.get('benefit_name', '')
        claim_type = claim.get('claim_type', '') or claim.get('type', '')

        if '延误' in benefit_name or 'delay' in claim_type.lower():
            return 'flight_delay'
        elif '行李' in benefit_name or 'baggage' in claim_type.lower():
            return 'baggage_damage'
        elif '医疗' in benefit_name or 'medical' in claim_type.lower():
            return 'medical'
        else:
            return 'flight_delay'  # 默认

    async def _download_claim(self, claim: Dict[str, Any]) -> bool:
        """
        下载单个案件

        Args:
            claim: 案件信息

        Returns:
            是否成功
        """
        forceid = claim.get('forceid') or claim.get('Id', '')
        claim_id = claim.get('claim_id', forceid)

        LOGGER.info(f"开始下载案件: {forceid}")

        # 创建案件目录
        case_dir = self.output_dir / f"案件号【{claim_id}】"
        case_dir.mkdir(parents=True, exist_ok=True)

        # 保存案件信息
        claim_info_path = case_dir / 'claim_info.json'
        try:
            with open(claim_info_path, 'w', encoding='utf-8') as f:
                json.dump(claim, f, ensure_ascii=False, indent=2)
        except Exception as e:
            LOGGER.error(f"保存案件信息失败: {e}")
            return False

        # 下载附件
        attachments = claim.get('attachments', []) or claim.get('files', []) or []
        downloaded_count = 0

        for idx, attachment in enumerate(attachments):
            url = attachment.get('url') or attachment.get('file_url', '')
            filename = attachment.get('filename') or attachment.get('name', f'file_{idx}')

            if not url:
                continue

            try:
                file_path = case_dir / filename
                if file_path.exists():
                    LOGGER.debug(f"文件已存在，跳过: {filename}")
                    downloaded_count += 1
                    continue

                # 下载文件
                success = await self._download_file(url, file_path)
                if success:
                    downloaded_count += 1
                    LOGGER.debug(f"下载成功: {filename}")

            except Exception as e:
                LOGGER.error(f"下载附件失败: {filename}, 错误: {e}")

        # 更新进度缓存
        self._progress_cache[forceid] = {
            'status': 'completed',
            'claim_status': ClaimStatus.DOWNLOADED.value,
            'downloaded_at': datetime.now().isoformat(),
            'downloaded_files': downloaded_count,
            'total_files': len(attachments),
            'claim_id': claim_id
        }
        self._save_progress()

        LOGGER.info(f"案件下载完成: {forceid}, 文件: {downloaded_count}/{len(attachments)}")
        return True

    async def _download_file(self, url: str, dest_path: Path) -> bool:
        """
        下载单个文件

        Args:
            url: 文件URL
            dest_path: 保存路径

        Returns:
            是否成功
        """
        try:
            response = requests.get(url, timeout=60, stream=True)

            if response.status_code != 200:
                LOGGER.error(f"文件下载失败: {response.status_code} - {url}")
                return False

            dest_path.parent.mkdir(parents=True, exist_ok=True)

            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return True

        except Exception as e:
            LOGGER.error(f"下载文件异常: {url}, 错误: {e}")
            return False

    async def retry_failed_downloads(self, limit: int = 10) -> Tuple[int, str]:
        """
        重试失败的下载

        Args:
            limit: 限制数量

        Returns:
            (重试成功数, 消息)
        """
        LOGGER.info(f"重试失败的下载任务 (限制: {limit})")

        # 获取失败的案件
        failed_claims = await self.status_manager.get_pending_claims(
            status_filter=[ClaimStatus.DOWNLOAD_FAILED],
            limit=limit
        )

        if not failed_claims:
            LOGGER.info("没有需要重试的失败案件")
            return 0, "没有需要重试的失败案件"

        LOGGER.info(f"找到 {len(failed_claims)} 个需要重试的案件")

        success_count = 0
        for claim in failed_claims:
            forceid = claim.get('forceid', '')

            try:
                # 重置状态
                await self.status_manager.update_download_status(
                    forceid,
                    DownloadStatus.RETRYING,
                    success=False,
                    error_message="重试下载"
                )

                # 重新下载
                # TODO: 从API重新获取案件信息
                success = await self._download_claim({"forceid": forceid, "claim_id": claim.get('claim_id')})

                if success:
                    success_count += 1
                    await self.status_manager.update_download_status(
                        forceid,
                        DownloadStatus.COMPLETED,
                        success=True
                    )
                else:
                    await self.status_manager.update_download_status(
                        forceid,
                        DownloadStatus.FAILED,
                        success=False,
                        error_message="重试失败"
                    )

            except Exception as e:
                LOGGER.error(f"重试下载失败: {forceid}, 错误: {e}")
                await self.status_manager.update_download_status(
                    forceid,
                    DownloadStatus.FAILED,
                    success=False,
                    error_message=str(e)
                )

        message = f"重试完成: 成功 {success_count}, 失败 {len(failed_claims) - success_count}"
        LOGGER.info(message)
        return success_count, message


# 全局实例
_download_scheduler = None


def get_download_scheduler() -> IncrementalDownloadScheduler:
    """获取增量下载调度器实例"""
    global _download_scheduler
    if _download_scheduler is None:
        _download_scheduler = IncrementalDownloadScheduler()
    return _download_scheduler


async def run_download_scheduler():
    """运行下载调度器（用于定时任务）"""
    scheduler = get_download_scheduler()
    await scheduler.initialize()

    try:
        count, message = await scheduler.run_hourly_check()
        return count, message
    finally:
        await scheduler.db.close()


if __name__ == '__main__':
    # 测试
    import asyncio

    async def test():
        scheduler = IncrementalDownloadScheduler()
        await scheduler.initialize()

        try:
            count, message = await scheduler.run_hourly_check()
            print(f"结果: {message}")
        finally:
            await scheduler.db.close()

    asyncio.run(test())
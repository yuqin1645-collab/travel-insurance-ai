#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产化主工作流
协调所有生产化组件，实现完整的案件处理流程
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config import config
from app.scheduler.download_scheduler import get_download_scheduler, IncrementalDownloadScheduler
from app.scheduler.review_scheduler import get_review_scheduler, ReviewScheduler
from app.supplementary.handler import get_supplementary_handler, SupplementaryHandler
from app.output.coordinator import get_output_coordinator, OutputCoordinator
from app.state.status_manager import get_status_manager, StatusManager
from app.db.database import get_db_connection

LOGGER = logging.getLogger(__name__)


class ProductionWorkflow:
    """生产化主工作流"""

    def __init__(
        self,
        download_scheduler: Optional[IncrementalDownloadScheduler] = None,
        review_scheduler: Optional[ReviewScheduler] = None,
        supplementary_handler: Optional[SupplementaryHandler] = None,
        output_coordinator: Optional[OutputCoordinator] = None,
        status_manager: Optional[StatusManager] = None
    ):
        self.download_scheduler = download_scheduler or get_download_scheduler()
        self.review_scheduler = review_scheduler or get_review_scheduler()
        self.supplementary_handler = supplementary_handler or get_supplementary_handler()
        self.output_coordinator = output_coordinator or get_output_coordinator()
        self.status_manager = status_manager or get_status_manager()
        self.db = get_db_connection()

        # 运行状态
        self.is_running = False
        self.last_run_time = None

    async def initialize(self):
        """初始化所有组件"""
        LOGGER.info("初始化生产化工作流...")

        # 初始化数据库连接
        await self.db.initialize()

        # 初始化各个调度器
        await self.download_scheduler.initialize()
        await self.review_scheduler.initialize()
        await self.supplementary_handler.initialize()
        await self.output_coordinator.initialize()

        LOGGER.info("✓ 生产化工作流初始化完成")

    async def run_hourly_check(self) -> Dict[str, Any]:
        """
        每小时执行的主检查流程

        Returns:
            执行结果汇总
        """
        if self.is_running:
            LOGGER.warning("上一次检查仍在运行，跳过本次检查")
            return {
                "status": "skipped",
                "reason": "上一次检查仍在运行"
            }

        self.is_running = True
        self.last_run_time = datetime.now()

        LOGGER.info("=" * 60)
        LOGGER.info("开始每小时检查流程")
        LOGGER.info("=" * 60)

        results = {
            "start_time": self.last_run_time.isoformat(),
            "tasks": {}
        }

        try:
            # 1. 增量下载新案件
            LOGGER.info("\n[1/5] 增量下载新案件...")
            download_count, download_msg = await self.download_scheduler.run_hourly_check()
            results["tasks"]["download"] = {
                "count": download_count,
                "message": download_msg
            }

            # 2. 审核待处理案件
            LOGGER.info("\n[2/5] 审核待处理案件...")
            review_count, review_msg = await self.review_scheduler.process_pending_reviews()
            results["tasks"]["review"] = {
                "count": review_count,
                "message": review_msg
            }

            # 3. 检查补件状态
            LOGGER.info("\n[3/5] 检查补件状态...")
            supplementary_result = await self.supplementary_handler.check_supplementary_deadline()
            results["tasks"]["supplementary_reminder"] = {
                "count": supplementary_result[0],
                "message": supplementary_result[1]
            }

            # 4. 检查补件超时
            LOGGER.info("\n[4/5] 检查补件超时...")
            timeout_result = await self.supplementary_handler.check_supplementary_timeout()
            results["tasks"]["supplementary_timeout"] = {
                "count": timeout_result[0],
                "message": timeout_result[1]
            }

            # 5. 检查收到补件
            LOGGER.info("\n[5/5] 检查收到补件...")
            received_result = await self.supplementary_handler.check_supplementary_received()
            results["tasks"]["supplementary_received"] = {
                "count": received_result[0],
                "message": received_result[1]
            }

            # 6. 同步审核结果到数据库
            LOGGER.info("\n[6/6] 同步审核结果到数据库...")
            sync_count, sync_fail = await asyncio.get_event_loop().run_in_executor(
                None, self._sync_review_results_to_db
            )
            results["tasks"]["sync_db"] = {
                "count": sync_count,
                "message": f"同步成功 {sync_count}，失败 {sync_fail}"
            }

            # 7. 同步人工处理状态
            LOGGER.info("\n[7/7] 同步人工处理状态...")
            manual_count, manual_fail = await asyncio.get_event_loop().run_in_executor(
                None, self._sync_manual_status
            )
            results["tasks"]["sync_manual"] = {
                "count": manual_count,
                "message": f"同步成功 {manual_count}，失败 {manual_fail}"
            }

            # 汇总结果
            results["end_time"] = datetime.now().isoformat()
            results["status"] = "success"
            results["summary"] = {
                "downloaded": download_count,
                "reviewed": review_count,
                "supplementary_reminded": supplementary_result[0],
                "supplementary_timeout": timeout_result[0],
                "supplementary_received": received_result[0],
                "db_synced": sync_count,
                "manual_synced": manual_count,
            }

            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("每小时检查流程完成")
            LOGGER.info("=" * 60)
            LOGGER.info(f"下载: {download_count} | 审核: {review_count} | 补件提醒: {supplementary_result[0]} | 超时: {timeout_result[0]} | 收到: {received_result[0]} | DB同步: {sync_count} | 人工状态: {manual_count}")
            LOGGER.info("=" * 60)

            return results

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"每小时检查流程异常: {error_msg}", exc_info=True)

            results["status"] = "failed"
            results["error"] = error_msg
            results["end_time"] = datetime.now().isoformat()

            return results

        finally:
            self.is_running = False

    def _sync_review_results_to_db(self) -> tuple:
        """同步本地审核结果JSON到数据库（同步方法，供executor调用）"""
        import json
        import pymysql
        import os
        from datetime import datetime as _dt

        results_dir = config.REVIEW_RESULTS_DIR
        claims_dir = config.CLAIMS_DATA_DIR

        # 构建 forceid -> claim_info 缓存
        info_cache = {}
        for f in claims_dir.rglob("claim_info.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                fid = str(data.get("forceid") or "").strip()
                if fid:
                    info_cache[fid] = data
            except Exception:
                pass

        # 加载所有审核结果
        json_files = list(results_dir.rglob("*_ai_review.json"))
        if not json_files:
            return 0, 0

        conn = pymysql.connect(
            host=os.getenv("DB_HOST", ""),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "ai"),
            charset="utf8mb4",
        )
        success = fail = 0
        try:
            with conn.cursor() as cur:
                for jf in json_files:
                    try:
                        data = json.loads(jf.read_text(encoding="utf-8"))
                        forceid = data.get("forceid", "")
                        if not forceid:
                            continue
                        claim_info = info_cache.get(forceid, {})
                        audit = data.get("flight_delay_audit") or data.get("DebugInfo", {}).get("flight_delay_audit") or {}
                        audit_result = audit.get("audit_result", "")
                        benefit_name = claim_info.get("BenefitName") or claim_info.get("benefit_name") or ""
                        remark = (data.get("Remark") or "")[:2000]
                        is_additional = str(data.get("IsAdditional", "N"))[:1]
                        key_conclusions = json.dumps(data.get("KeyConclusions", []), ensure_ascii=False)
                        raw_result = json.dumps(data, ensure_ascii=False)
                        claim_id = data.get("ClaimId") or data.get("claim_id") or claim_info.get("ClaimId") or ""
                        cur.execute(
                            """INSERT INTO ai_review_result
                               (forceid, claim_id, benefit_name, remark, is_additional,
                                audit_result, key_conclusions, raw_result)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                               ON DUPLICATE KEY UPDATE
                                 claim_id=VALUES(claim_id),
                                 benefit_name=VALUES(benefit_name),
                                 remark=VALUES(remark),
                                 is_additional=VALUES(is_additional),
                                 audit_result=VALUES(audit_result),
                                 key_conclusions=VALUES(key_conclusions),
                                 raw_result=VALUES(raw_result),
                                 updated_at=CURRENT_TIMESTAMP""",
                            (forceid, claim_id, benefit_name, remark, is_additional,
                             audit_result, key_conclusions, raw_result)
                        )
                        success += 1
                    except Exception as e:
                        fail += 1
                        LOGGER.warning(f"同步审核结果失败 {jf.name}: {e}")
            conn.commit()
        finally:
            conn.close()
        return success, fail

    def _sync_manual_status(self) -> tuple:
        """查询人工处理状态并更新数据库（同步方法，供executor调用）"""
        import os
        import requests
        import pymysql

        RESULT_API_URL = "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim_Result"

        conn = pymysql.connect(
            host=os.getenv("DB_HOST", ""),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "ai"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        success = fail = 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT forceid FROM ai_review_result")
                rows = cur.fetchall()

            if not rows:
                return 0, 0

            forceids = [row["forceid"] for row in rows]

            # 批量查询接口
            try:
                resp = requests.post(
                    RESULT_API_URL,
                    json={"pageSize": "100", "pageIndex": "1", "data": forceids},
                    timeout=30,
                )
                resp.raise_for_status()
                raw = resp.json()
                if isinstance(raw, list):
                    items = raw
                elif isinstance(raw, dict):
                    inner = raw.get("data")
                    items = inner if isinstance(inner, list) else [raw]
                else:
                    items = []

                result_map = {}
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    fid = str(item.get("forceid") or item.get("ForceId") or "").strip()
                    if fid:
                        result_map[fid] = item
            except Exception as e:
                LOGGER.warning(f"批量查询人工状态接口失败: {e}")
                return 0, len(forceids)

            for forceid in forceids:
                data = result_map.get(forceid)
                if not data:
                    continue
                try:
                    final_status = str(data.get("Final_Status") or "").strip()
                    supplementary_reason = str(data.get("Supplementary_Reason") or "").strip()
                    approved = str(data.get("Approved_amount") or "").strip()
                    assessment_remark = str(data.get("Assessment_Remark") or "").strip()

                    if final_status == "事后理赔拒赔":
                        manual_status, manual_conclusion = "拒绝", assessment_remark
                    elif final_status == "待补件":
                        manual_status, manual_conclusion = "需补齐资料", supplementary_reason
                    elif final_status == "支付成功":
                        manual_status, manual_conclusion = "通过", approved
                    else:
                        # 非最终状态（如线上理赔初审），先按通过处理，后续同步会覆盖
                        manual_status, manual_conclusion = "通过", final_status

                    with conn.cursor() as cur:
                        cur.execute(
                            """UPDATE ai_review_result
                               SET manual_status=%s, manual_conclusion=%s, updated_at=CURRENT_TIMESTAMP
                               WHERE forceid=%s""",
                            (manual_status, manual_conclusion, forceid)
                        )
                    conn.commit()
                    success += 1
                except Exception as e:
                    fail += 1
                    LOGGER.warning(f"同步人工状态失败 {forceid}: {e}")
        finally:
            conn.close()
        return success, fail

    async def process_single_claim(
        self,
        claim_type: str,
        claim_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        处理单个案件的完整流程

        Args:
            claim_type: 案件类型
            claim_data: 案件数据

        Returns:
            处理结果
        """
        forceid = claim_data.get("forceid", "")
        LOGGER.info(f"处理单个案件: {forceid} ({claim_type})")

        results = {
            "forceid": forceid,
            "claim_type": claim_type,
            "steps": []
        }

        try:
            # 1. 创建案件状态
            await self.status_manager.create_claim_status(
                claim_id=claim_data.get("claim_id", forceid),
                forceid=forceid,
                claim_type=claim_type,
                initial_status="download_pending"
            )
            results["steps"].append({"step": "create_status", "status": "success"})

            # 2. 执行审核
            # 这里需要调用实际的审核逻辑
            # review_result = await self._review_claim(claim_type, claim_data)
            review_result = {"forceid": forceid, "Remark": "测试结果", "IsAdditional": "N"}  # 示例

            results["steps"].append({"step": "review", "status": "success", "result": review_result})

            # 3. 处理结果
            if review_result.get("IsAdditional") == "Y":
                # 需补件
                await self.supplementary_handler.handle_supplementary_needed(forceid, review_result)
                results["steps"].append({"step": "supplementary", "status": "success"})
            else:
                # 最终决定，输出结果
                output_result = await self.output_coordinator.dispatch_output(forceid, review_result)
                results["steps"].append({"step": "output", "status": "success", "result": output_result})

            results["overall_status"] = "success"
            LOGGER.info(f"✓ 单个案件处理完成: {forceid}")

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"✗ 单个案件处理失败: {forceid}, 错误: {error_msg}")
            results["overall_status"] = "failed"
            results["error"] = error_msg

        return results

    async def run_cleanup(self, days_to_keep: int = 30) -> Dict[str, Any]:
        """
        运行清理任务

        Args:
            days_to_keep: 保留天数

        Returns:
            清理结果
        """
        LOGGER.info(f"运行清理任务: 保留 {days_to_keep} 天数据")

        results = {
            "days_to_keep": days_to_keep,
            "tasks": {}
        }

        try:
            # 1. 清理过期案件状态
            count, msg = await self.status_manager.cleanup_expired_claims(days_to_keep)
            results["tasks"]["claim_status"] = {"count": count, "message": msg}

            # 2. 清理下载进度缓存
            # TODO: 实现清理逻辑

            # 3. 清理临时文件
            # TODO: 实现清理逻辑

            results["status"] = "success"
            LOGGER.info("清理任务完成")

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"清理任务失败: {error_msg}")
            results["status"] = "failed"
            results["error"] = error_msg

        return results

    async def get_system_status(self) -> Dict[str, Any]:
        """
        获取系统状态

        Returns:
            系统状态信息
        """
        LOGGER.info("获取系统状态...")

        status = {
            "timestamp": datetime.now().isoformat(),
            "is_running": self.is_running,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
            "components": {
                "database": "connected" if self.db.pool else "disconnected",
                "download_scheduler": "initialized",
                "review_scheduler": "initialized",
                "supplementary_handler": "initialized",
                "output_coordinator": "initialized"
            },
            "config": {
                "download_interval": config.DOWNLOAD_INTERVAL,
                "review_interval": config.REVIEW_INTERVAL,
                "supplementary_check_interval": config.SUPPLEMENTARY_CHECK_INTERVAL,
                "max_supplementary_count": config.MAX_SUPPLEMENTARY_COUNT
            }
        }

        # 获取案件统计
        try:
            statistics = await self.status_manager.get_claim_statistics()
            status["statistics"] = statistics
        except Exception as e:
            LOGGER.error(f"获取案件统计失败: {e}")
            status["statistics"] = {"error": str(e)}

        return status

    async def shutdown(self):
        """关闭系统"""
        LOGGER.info("关闭生产化工作流...")

        self.is_running = False

        # 关闭数据库连接
        await self.db.close()

        LOGGER.info("✓ 生产化工作流已关闭")


# 全局实例
_production_workflow = None


def get_production_workflow() -> ProductionWorkflow:
    """获取生产化工作流实例"""
    global _production_workflow
    if _production_workflow is None:
        _production_workflow = ProductionWorkflow()
    return _production_workflow


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='航班延误AI审核系统生产化工作流')
    parser.add_argument('--mode', type=str, default='hourly',
                       choices=['hourly', 'status', 'cleanup', 'single'],
                       help='运行模式')
    parser.add_argument('--days', type=int, default=30,
                       help='清理模式下的保留天数')
    parser.add_argument('--forceid', type=str,
                       help='单个案件模式下的案件ID')

    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('production.log', encoding='utf-8')
        ]
    )

    # 创建工作流
    workflow = get_production_workflow()

    try:
        # 初始化
        await workflow.initialize()

        # 根据模式执行
        if args.mode == 'hourly':
            result = await workflow.run_hourly_check()
            print(f"\n结果: {result}")

        elif args.mode == 'status':
            status = await workflow.get_system_status()
            print(f"\n系统状态: {status}")

        elif args.mode == 'cleanup':
            result = await workflow.run_cleanup(args.days)
            print(f"\n清理结果: {result}")

        elif args.mode == 'single':
            if not args.forceid:
                print("错误: 单个案件模式需要指定 --forceid")
                return

            # TODO: 从数据库或文件加载案件数据
            claim_data = {"forceid": args.forceid}
            result = await workflow.process_single_claim("flight_delay", claim_data)
            print(f"\n处理结果: {result}")

    except KeyboardInterrupt:
        LOGGER.info("用户中断")
    except Exception as e:
        LOGGER.error(f"主流程异常: {e}", exc_info=True)
    finally:
        await workflow.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
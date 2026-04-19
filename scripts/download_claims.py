#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理赔材料下载工具
支持断点续传、自动文件类型检测、结构化存储（同步版 + 异步版）
"""

import os
import json
import time
import logging
import requests
import aiohttp
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# 异步模式下需要全局 session 引用
_async_session: Optional[aiohttp.ClientSession] = None

# 文件下载重试配置
DOWNLOAD_MAX_RETRIES = 3          # 单文件最多重试次数
DOWNLOAD_RETRY_DELAY = 2          # 重试间隔（秒）
DOWNLOAD_TIMEOUT = 120             # 单文件下载超时（秒）

LOGGER = logging.getLogger(__name__)


# 文件魔数 → 扩展名映射（用于无后缀文件类型检测）
MAGIC_BYTES: Dict[bytes, str] = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"%PDF": ".pdf",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"BM": ".bmp",
    b"PK\x03\x04": ".zip",
    b"Rar!": ".rar",
    b"\x00\x00\x00\x0cftyp": ".mp4",
    b"\x1f\x8b": ".gz",
}


def detect_extension(data: bytes) -> str:
    """根据文件头魔数检测文件类型，返回带点的扩展名，无法识别则返回 .bin"""
    for magic, ext in MAGIC_BYTES.items():
        if data[:len(magic)] == magic:
            return ext
    return ".bin"


class ClaimDownloader:
    def __init__(self, api_url: str, output_dir: str = "claims_data", force_refresh: bool = False):
        self.api_url = api_url
        self.output_dir = Path(output_dir)
        self.force_refresh = force_refresh
        self.progress_file = self.output_dir / ".download_progress.json"
        self.progress: Dict = self._load_progress()

    # ------------------------------------------------------------------ #
    #  进度文件读写
    # ------------------------------------------------------------------ #

    def _load_progress(self) -> Dict:
        """加载下载进度文件，不存在则返回空字典"""
        if self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                LOGGER.warning(f"[警告] 进度文件读取失败，将从头开始: {e}")
        return {}

    def _save_progress(self) -> None:
        """将当前进度写回磁盘"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    #  API 调用
    # ------------------------------------------------------------------ #

    def fetch_claims(self, payload: dict) -> List[Dict]:
        """调用 POST 接口获取全部理赔案件列表（自动分页）"""
        page_size = int(payload.get("pageSize", 30))
        all_claims: List[Dict] = []
        page_index = 1

        while True:
            paged_payload = {**payload, "pageSize": str(page_size), "pageIndex": str(page_index)}
            try:
                resp = requests.post(self.api_url, json=paged_payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                LOGGER.error(f"[错误] API 请求失败 (第{page_index}页): {e}")
                break

            # 提取分页元信息
            total_count = None
            total_page = None
            records: List[Dict] = []

            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                total_count = data.get("totalCount")
                total_page = data.get("totalPage")
                for key in ("records", "data", "result", "claims"):
                    if key in data and isinstance(data[key], list):
                        records = data[key]
                        break
                if not records:
                    # 尝试把 dict 本身当作单条记录（API 只返回一条案件时）
                    if data.get("CaseNo") or data.get("forceid") or data.get("Id"):
                        records = [data]
                        LOGGER.info("[信息] API 返回单条记录 dict，已按单条处理")
                    else:
                        LOGGER.warning(f"[警告] 未识别的 API 返回格式: {type(data)}，keys={list(data.keys())[:10]}")
                        break

            all_claims.extend(records)

            # 打印分页进度
            if total_count is not None:
                LOGGER.info(f"  第{page_index}/{total_page or '?'}页，本页 {len(records)} 条，累计 {len(all_claims)}/{total_count}")
            else:
                LOGGER.info(f"  第{page_index}页，本页 {len(records)} 条，累计 {len(all_claims)}")

            # 终止条件：本页无数据 / 已到最后一页 / 已拉完全部
            if not records:
                break
            if total_page is not None and page_index >= total_page:
                break
            if total_count is not None and len(all_claims) >= total_count:
                break
            # 兜底：本页条数不足一页，说明已是最后一页
            if len(records) < page_size:
                break

            page_index += 1

        return all_claims

    # ------------------------------------------------------------------ #
    #  文件下载
    # ------------------------------------------------------------------ #

    def _download_file(self, url: str, dest_path: Path) -> bool:
        """
        下载单个文件到 dest_path，自动补全无后缀文件的扩展名。
        返回 True 表示成功，False 表示失败。
        重试策略: 最多 DOWNLOAD_MAX_RETRIES 次，间隔 DOWNLOAD_RETRY_DELAY 秒。
        """
        import time as _time
        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
                resp.raise_for_status()
                file_content = resp.content

                _dest = dest_path
                if not _dest.suffix:
                    ext = detect_extension(file_content)
                    _dest = _dest.with_suffix(ext)

                _dest.parent.mkdir(parents=True, exist_ok=True)
                _dest.write_bytes(file_content)
                if attempt > 1:
                    LOGGER.info(f"[重试成功] {_dest.name} 第{attempt}次")
                return True

            except requests.RequestException as e:
                if attempt < DOWNLOAD_MAX_RETRIES:
                    LOGGER.warning(f"[重试] {dest_path.name} 第{attempt}次失败，{DOWNLOAD_RETRY_DELAY}s后重试: {e}")
                    _time.sleep(DOWNLOAD_RETRY_DELAY)
                else:
                    LOGGER.error(f"[失败] 下载 {url} -> {e}")

            except IOError as e:
                if attempt < DOWNLOAD_MAX_RETRIES:
                    LOGGER.warning(f"[重试] 写入 {dest_path.name} 第{attempt}次失败: {e}")
                    _time.sleep(DOWNLOAD_RETRY_DELAY)
                else:
                    LOGGER.error(f"[失败] 写入文件 {dest_path} -> {e}")

        return False

    # ------------------------------------------------------------------ #
    #  核心处理逻辑
    # ------------------------------------------------------------------ #

    def _get_case_dir(self, benefit_name: str, case_no: str) -> Path:
        """返回案件文件夹路径，格式：{output_dir}/{BenefitName}/{BenefitName}-案件号【{CaseNo}】"""
        return self.output_dir / benefit_name / f"{benefit_name}-案件号【{case_no}】"

    def _is_file_downloaded(self, case_no: str, file_id: str, case_dir: Path) -> bool:
        """判断某个文件是否已经成功下载。
        验证逻辑：① 进度文件记录了 ID  ② 磁盘上对应文件真实存在（防止记录存在但文件已丢失）
        """
        record = self.progress.get(case_no, {})
        downloaded = record.get("downloadedFiles", [])
        file_stem = Path(file_id).stem
        if not any(Path(f).stem == file_stem for f in downloaded):
            return False
        # 进度文件有记录 → 再验证磁盘上文件是否真实存在
        if not case_dir.exists():
            return False
        for f in case_dir.iterdir():
            if f.is_file() and f.stem == file_stem:
                return True
        return False

    def process_claim(self, claim: Dict) -> None:
        """处理单条理赔记录：只下载缺失/失败的文件，并更新进度 JSON 中的新增字段"""
        case_no: str = str(claim.get("CaseNo") or claim.get("caseNo") or claim.get("PolicyNo") or claim.get("policyNo") or "").strip()
        benefit_name: str = str(claim.get("BenefitName") or claim.get("benefitName") or "").strip()
        applicant_name: str = str(claim.get("ApplicantName") or claim.get("applicantName") or claim.get("Applicant_Name") or "").strip()

        if not case_no or not benefit_name:
            LOGGER.warning(f"[跳过] 缺少 CaseNo 或 BenefitName: {claim}")
            return

        # 获取文件列表，兼容多种字段名（API 新版本使用 FileList）
        files: List[Dict] = (
            claim.get("FileList")
            or claim.get("Files")
            or claim.get("files")
            or claim.get("Attachments")
            or claim.get("attachments")
            or []
        )

        case_dir = self._get_case_dir(benefit_name, case_no)
        existing_record = self.progress.get(case_no, {})

        # force_refresh 模式：删除旧 claim_info.json，附件文件也重新下载
        if self.force_refresh:
            claim_info_path_old = case_dir / "claim_info.json"
            if claim_info_path_old.exists():
                claim_info_path_old.unlink()
            existing_record["downloadedFiles"] = []
            existing_record["failedFiles"] = []
            existing_record["status"] = "pending"
            LOGGER.info(f"    [强制刷新] 清除下载进度，重新下载所有附件")

        # ---------- 自愈检查：进度文件说「已完成」但磁盘没有材料文件 → 重置状态 ----------
        # 场景：①磁盘文件被误删 ②之前某次下载中途失败 ③FileList字段未识别导致0文件标记为完成
        existing_record = self.progress.get(case_no, {})
        has_real_files = any(
            f.is_file() and f.name != "claim_info.json"
            for f in case_dir.iterdir()
        ) if case_dir.exists() else False
        was_completed_but_empty = (
            existing_record.get("status") == "completed"
            and existing_record.get("totalFiles", 0) == 0
            and not has_real_files
        )
        was_completed_but_missing_materials = (
            existing_record.get("status") == "completed"
            and existing_record.get("downloadedFiles", []) == []
            and len(files) > 0
            and not has_real_files
        )
        if was_completed_but_empty or was_completed_but_missing_materials:
            LOGGER.info(f"    [自愈] 进度已完成但磁盘无材料文件，重置下载状态（FileList字段识别修复后触发）")
            existing_record["downloadedFiles"] = []
            existing_record["failedFiles"] = []
            existing_record["status"] = "pending"

        # ---------- 将案件基本信息写入 claim_info.json ----------
        claim_info_path = case_dir / "claim_info.json"
        # 构建要保存的 claim_info（所有字段，FileList 保留原始结构）
        claim_info = {k: v for k, v in claim.items()}
        # FileList 字段名统一（兼容 Files/files/Attachments）
        for fkey in ("Files", "files", "Attachments", "attachments"):
            if fkey in claim_info and fkey != "FileList":
                claim_info["FileList"] = claim_info.pop(fkey)

        # ---------- 计算拒赔金额 ----------
        final_status: str = str(claim.get("Final_Status") or claim.get("final_status") or "")
        if "拒赔" in final_status or "部分" in final_status:
            try:
                apply_amount = float(claim.get("Amount") or claim.get("amount") or 0)
                approved_amount = float(claim.get("Reserved_Amount") or claim.get("reserved_amount") or 0)
                rejected_amount = apply_amount - approved_amount
                claim_info["Rejected_Amount"] = f"{rejected_amount:.2f}"
            except (ValueError, TypeError):
                pass

        case_dir.mkdir(parents=True, exist_ok=True)
        with open(claim_info_path, "w", encoding="utf-8") as f:
            json.dump(claim_info, f, ensure_ascii=False, indent=4)

        # ---------- 将原始字段写入 ai_claim_info_raw 表（数据追溯备份） ----------
        try:
            _save_claim_info_to_db(claim_info)
        except Exception as _db_err:
            LOGGER.warning(f"claim_info 写库失败（不影响下载）: {_db_err}")

        # ---------- 更新进度记录中的已知字段（新增字段直接合并） ----------
        # 把 claim 中所有字段（除 Files/files）写入进度，但不覆盖下载状态字段
        protected_keys = {"totalFiles", "downloadedFiles", "failedFiles", "status", "startTime", "completedTime"}
        for k, v in claim.items():
            if k.lower() in ("files", "attachments"):
                continue
            # 字段名统一转为 camelCase 小写首字母存储
            progress_key = k[0].lower() + k[1:] if k else k
            if progress_key not in protected_keys:
                existing_record[progress_key] = v

        # 记录本次 API 返回的 fileList（用于 fix_empty_downloads 判断「是否真的应该有附件」）
        existing_record["fileList"] = files

        # 补齐固定字段
        existing_record.setdefault("applicantName", applicant_name)
        existing_record.setdefault("benefitName", benefit_name)
        existing_record.setdefault("totalFiles", len(files))
        existing_record.setdefault("downloadedFiles", [])
        existing_record.setdefault("failedFiles", [])
        existing_record.setdefault("status", "pending")
        existing_record.setdefault("startTime", datetime.now().isoformat())

        # 如果文件总数变了（新 API 返回更多文件），更新 totalFiles
        existing_record["totalFiles"] = len(files)

        # ---------- 确定哪些文件需要下载 ----------
        downloaded_files: List[str] = existing_record["downloadedFiles"]
        failed_files: List[str] = existing_record["failedFiles"]

        # 需要下载 = 未下载 + 上次失败
        files_to_download = [
            f for f in files
            if not self._is_file_downloaded(case_no, self._get_file_id(f), case_dir)
        ]

        if not files_to_download:
            # 所有文件都已下载，只需更新 JSON 中的新增字段
            LOGGER.info(f"[已完成] {benefit_name}-{case_no}（{applicant_name}）文件已全部下载，更新字段")
            existing_record["status"] = "completed"
            self.progress[case_no] = existing_record
            self._save_progress()
            return

        # 有新文件（或失败重试）需要下载
        if existing_record["status"] == "completed":
            LOGGER.info(f"[新增文件] {benefit_name}-{case_no}（{applicant_name}）发现 {len(files_to_download)} 个未下载文件")
        else:
            LOGGER.info(f"[下载中] {benefit_name}-{case_no}（{applicant_name}）共 {len(files)} 个文件，需下载 {len(files_to_download)} 个")

        existing_record["status"] = "in_progress"
        self.progress[case_no] = existing_record

        # ---------- 逐文件下载 ----------
        newly_failed: List[str] = []
        newly_downloaded: List[str] = []

        for file_info in files_to_download:
            file_id = self._get_file_id(file_info)
            file_url = self._get_file_url(file_info)
            file_name = self._get_file_name(file_info)

            if not file_url:
                LOGGER.debug(f"    [跳过] 文件无 URL: {file_info}")
                continue

            # 确定保存路径（无后缀时先用 file_id，下载后再补扩展名）
            dest_name = _sanitize_filename(file_name or file_id)
            dest_path = case_dir / dest_name

            LOGGER.info(f"    ↓ {dest_name[:60]}{'...' if len(dest_name) > 60 else ''}")

            if self._download_file(file_url, dest_path):
                # 记录实际保存的文件名（可能已加扩展名）
                # 找磁盘上实际生成的文件名
                actual_name = self._find_actual_filename(case_dir, Path(dest_name).stem)
                newly_downloaded.append(actual_name or dest_name)
                # 从 failed 列表移除（如果之前失败过）
                failed_files = [f for f in failed_files if Path(f).stem != Path(file_id).stem]
            else:
                newly_failed.append(file_id)

            time.sleep(0.2)  # 适当限速，避免被服务端限流

        # ---------- 更新进度 ----------
        # 合并后去重（以 stem 为唯一键），防止补件重新下载时文件名重复累积
        merged = {Path(f).stem: f for f in downloaded_files}
        for f in newly_downloaded:
            merged[Path(f).stem] = f
        existing_record["downloadedFiles"] = list(merged.values())
        existing_record["failedFiles"] = list(set(failed_files + newly_failed))

        all_done = len(existing_record["downloadedFiles"]) >= len(files) and not existing_record["failedFiles"]
        existing_record["status"] = "completed" if all_done else "partial"
        if all_done:
            existing_record["completedTime"] = datetime.now().isoformat()

        self.progress[case_no] = existing_record
        self._save_progress()
        status_icon = "✓" if all_done else "⚠"
        LOGGER.info(f"  {status_icon} {case_no}: 本次下载 {len(newly_downloaded)}，失败 {len(newly_failed)}，累计 {len(existing_record['downloadedFiles'])}/{len(files)}")

    @staticmethod
    def _get_file_id(file_info: Dict) -> str:
        """从 file_info 中提取可靠的唯一标识（避免使用完整 URL 作为 ID）。
        优先级：FileId > fileId > Id > id > url路径末段(解码后) > 空字符串"""
        explicit = (
            file_info.get("FileId") or file_info.get("fileId") or
            file_info.get("Id") or file_info.get("id") or ""
        ).strip()
        if explicit:
            return explicit

        # 用 URL 路径末尾（不含参数）作为兜底标识，并做 URL 解码
        # 重要：需要解码以匹配磁盘上的文件名（_get_file_name 会解码）
        url = (
            file_info.get("FileUrl") or file_info.get("fileUrl") or
            file_info.get("url") or ""
        ).strip()
        if url:
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if path:
                return unquote(path.split("/")[-1])
        return ""

    @staticmethod
    def _get_file_url(file_info: Dict) -> str:
        return str(
            file_info.get("Url") or file_info.get("url") or
            file_info.get("FileUrl") or file_info.get("fileUrl") or
            file_info.get("FileURL") or file_info.get("fileURL") or
            file_info.get("DownloadUrl") or file_info.get("downloadUrl") or
            ""
        ).strip()

    def _get_file_name(self, file_info: Dict) -> str:
        """从 file_info 中提取用于保存磁盘的文件名，兜底策略：
        1. API 原生 FileName/Name 字段
        2. URL 路径末段（去除 query 参数）
        3. filebridge.alipay.com 等 fileKey 参数中提取
        4. 兜底空字符串（文件名将由 file_id 在 download 时确定）
        """
        # 1. API 字段
        name = str(
            file_info.get("FileName") or file_info.get("fileName") or
            file_info.get("Name") or file_info.get("name") or ""
        ).strip()
        if name:
            return name

        url = self._get_file_url(file_info)
        if url:
            from urllib.parse import urlparse, parse_qs, unquote
            parsed = urlparse(url)

            # 2. URL 路径末段
            path = parsed.path.rstrip("/")
            if path:
                fname = path.split("/")[-1]
                if fname and "." in fname:
                    return unquote(fname)

            # 3. filebridge 域名从 fileKey 参数中提取真实文件名
            if "filebridge.alipay.com" in parsed.netloc:
                params = parse_qs(parsed.query)
                file_keys = params.get("fileKey", [])
                if file_keys:
                    fk = file_keys[0].rstrip("/")
                    if fk:
                        fname = fk.split("/")[-1]
                        if fname:
                            return unquote(fname)

            # 4. 其他 URL 尝试 query 中常见的 filename 参数
            params = parse_qs(parsed.query)
            for key in ("filename", "name", "file"):
                vals = params.get(key, [])
                if vals and vals[0]:
                    return unquote(vals[0])

        return ""

    @staticmethod
    def _find_actual_filename(folder: Path, stem: str) -> Optional[str]:
        """在 folder 中查找以 stem 开头的文件（下载时可能追加了扩展名）"""
        if not folder.exists():
            return None
        for f in folder.iterdir():
            if f.stem == stem:
                return f.name
        return None

    # ------------------------------------------------------------------ #
    #  主入口
    # ------------------------------------------------------------------ #

    def run(self, payload: dict) -> None:
        """拉取全部理赔数据并逐条处理"""
        LOGGER.info(f"[开始] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 拉取理赔数据...")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        claims = self.fetch_claims(payload)
        if not claims:
            LOGGER.info(f"[结束] 未获取到任何理赔记录")
            return

        LOGGER.info(f"[获取] 共 {len(claims)} 条理赔记录")

        for idx, claim in enumerate(claims, 1):
            case_no = claim.get("CaseNo") or claim.get("caseNo") or "?"
            LOGGER.info(f"[{idx}/{len(claims)}] 处理案件 {case_no}")
            self.process_claim(claim)
            print()

        # 汇总
        total = len(self.progress)
        completed = sum(1 for v in self.progress.values() if v.get("status") == "completed")
        partial = sum(1 for v in self.progress.values() if v.get("status") == "partial")
        LOGGER.info(f"[汇总] 总案件: {total}，完成: {completed}，部分失败: {partial}")
        LOGGER.info(f"[结束] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ====================================================================== #
#  异步下载版本（非阻塞，不会阻塞事件循环）
# ====================================================================== #

class AsyncClaimDownloader:
    """
    异步版理赔材料下载器
    - 使用 aiohttp 替代 requests，支持并发下载文件
    - 主流程不再使用 print，全部通过 LOGGER 输出
    - 兼容 ClaimDownloader 的进度文件结构，可共享
    """

    def __init__(
        self,
        api_url: str,
        output_dir: str = "claims_data",
        force_refresh: bool = False,
        max_concurrent_downloads: int = 10,
    ):
        self.api_url = api_url
        self.output_dir = Path(output_dir)
        self.force_refresh = force_refresh
        self.max_concurrent = max_concurrent_downloads
        self.progress_file = self.output_dir / ".download_progress.json"
        self.progress: Dict = self._load_progress()

    # ---------- 进度文件 ---------- #
    def _load_progress(self) -> Dict:
        if self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                LOGGER.warning(f"[警告] 进度文件读取失败，将从头开始: {e}")
        return {}

    def _save_progress(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    # ---------- API 调用 ---------- #
    async def fetch_claims(self, payload: dict) -> List[Dict]:
        page_size = int(payload.get("pageSize", 30))
        all_claims: List[Dict] = []
        page_index = 1

        while True:
            paged_payload = {**payload, "pageSize": str(page_size), "pageIndex": str(page_index)}
            try:
                async with _async_session.post(
                    self.api_url, json=paged_payload, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        LOGGER.error(f"[错误] API 请求失败 (第{page_index}页): HTTP {resp.status}")
                        break
                    data = await resp.json()
            except asyncio.TimeoutError:
                LOGGER.error(f"[错误] API 请求超时 (第{page_index}页)")
                break
            except Exception as e:
                LOGGER.error(f"[错误] API 请求异常 (第{page_index}页): {e}")
                break

            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = []
                for key in ("records", "data", "result", "claims"):
                    if key in data and isinstance(data[key], list):
                        records = data[key]
                        break
                if not records:
                    if data.get("CaseNo") or data.get("forceid") or data.get("Id"):
                        records = [data]
                        LOGGER.info("[信息] API 返回单条记录 dict，已按单条处理")
                    else:
                        LOGGER.warning(f"[警告] ��识别的 API 返回格式: {type(data)}，keys={list(data.keys())[:10]}")
                        break
            else:
                LOGGER.error(f"[错误] API 返回类型不支持: {type(data)}")
                break

            all_claims.extend(records)

            total_count = data.get("totalCount") if isinstance(data, dict) else None
            total_page = data.get("totalPage") if isinstance(data, dict) else None

            if total_count is not None:
                LOGGER.info(f"  第{page_index}/{total_page or '?'}页，本页 {len(records)} 条，累计 {len(all_claims)}/{total_count}")
            else:
                LOGGER.info(f"  第{page_index}页，本页 {len(records)} 条，累计 {len(all_claims)}")

            if not records:
                break
            if total_page is not None and page_index >= total_page:
                break
            if total_count is not None and len(all_claims) >= total_count:
                break
            if len(records) < page_size:
                break

            page_index += 1

        return all_claims

    # ---------- 单文件下载 ---------- #
    async def _download_file_async(self, url: str, dest_path: Path) -> bool:
        """
        异步下载单个文件到 dest_path，超时后自动重试（最多 DOWNLOAD_MAX_RETRIES 次）。
        """
        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                async with _async_session.get(
                    url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
                ) as resp:
                    if resp.status != 200:
                        if attempt < DOWNLOAD_MAX_RETRIES:
                            LOGGER.warning(f"[重试] {dest_path.name} HTTP {resp.status}，{DOWNLOAD_RETRY_DELAY}s后重试")
                            await asyncio.sleep(DOWNLOAD_RETRY_DELAY)
                            continue
                        else:
                            LOGGER.error(f"[失败] 文件下载失败: HTTP {resp.status} - {url}")
                            return False
                    content = await resp.read()

                _dest = dest_path
                if not _dest.suffix:
                    ext = detect_extension(content)
                    _dest = _dest.with_suffix(ext)

                _dest.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(_dest.write_bytes, content)
                if attempt > 1:
                    LOGGER.info(f"[重试成功] {_dest.name} 第{attempt}次")
                return True

            except asyncio.TimeoutError:
                if attempt < DOWNLOAD_MAX_RETRIES:
                    LOGGER.warning(f"[重试] {dest_path.name} 超时，{DOWNLOAD_RETRY_DELAY}s后重试 ({attempt}/{DOWNLOAD_MAX_RETRIES})")
                    await asyncio.sleep(DOWNLOAD_RETRY_DELAY)
                else:
                    LOGGER.error(f"[失败] 文件下载超时: {url}")
                    return False

            except Exception as e:
                if attempt < DOWNLOAD_MAX_RETRIES:
                    LOGGER.warning(f"[重试] {dest_path.name} 异常，{DOWNLOAD_RETRY_DELAY}s后重试: {e}")
                    await asyncio.sleep(DOWNLOAD_RETRY_DELAY)
                else:
                    LOGGER.error(f"[失败] 下载文件异常: {url}, 错误: {e}")
                    return False

        return False

    # ---------- 单案件处理 ---------- #
    async def _process_claim_async(self, claim: Dict, semaphore: asyncio.Semaphore) -> None:
        """处理单个案件（含并发文件下载）"""
        async with semaphore:
            case_no: str = str(
                claim.get("CaseNo") or claim.get("caseNo") or
                claim.get("PolicyNo") or claim.get("policyNo") or ""
            ).strip()
            benefit_name: str = str(claim.get("BenefitName") or claim.get("benefitName") or "").strip()
            applicant_name: str = str(
                claim.get("ApplicantName") or claim.get("applicantName") or
                claim.get("Applicant_Name") or ""
            ).strip()

            if not case_no or not benefit_name:
                LOGGER.warning(f"[跳过] 缺少 CaseNo 或 BenefitName: {claim}")
                return

            LOGGER.info(f"[处理中] {benefit_name}-{case_no}（{applicant_name}）")

            case_dir = self._get_case_dir(benefit_name, case_no)
            existing_record = self.progress.get(case_no, {})

            # ---------- force_refresh ----------
            if self.force_refresh:
                claim_info_path_old = case_dir / "claim_info.json"
                if claim_info_path_old.exists():
                    claim_info_path_old.unlink()
                existing_record["downloadedFiles"] = []
                existing_record["failedFiles"] = []
                existing_record["status"] = "pending"
                LOGGER.info(f"    [强制刷新] 清除下载进度，重新下载所有���件")

            # ---------- 自愈检查 ----------
            existing_record = self.progress.get(case_no, {})
            files: List[Dict] = (
                claim.get("FileList")
                or claim.get("Files")
                or claim.get("files")
                or claim.get("Attachments")
                or claim.get("attachments")
                or []
            )
            has_real_files = any(
                f.is_file() and f.name != "claim_info.json"
                for f in case_dir.iterdir()
            ) if case_dir.exists() else False

            was_completed_but_empty = (
                existing_record.get("status") == "completed"
                and existing_record.get("totalFiles", 0) == 0
                and not has_real_files
            )
            was_completed_but_missing_materials = (
                existing_record.get("status") == "completed"
                and existing_record.get("downloadedFiles", []) == []
                and len(files) > 0
                and not has_real_files
            )

            if was_completed_but_empty or was_completed_but_missing_materials:
                LOGGER.info(f"    [自愈] 进度已完成但磁盘无材料文件，重置下载状态")
                existing_record["downloadedFiles"] = []
                existing_record["failedFiles"] = []
                existing_record["status"] = "pending"

            # ---------- 保存 claim_info.json ----------
            claim_info_path = case_dir / "claim_info.json"
            claim_info = {k: v for k, v in claim.items()}
            for fkey in ("Files", "files", "Attachments", "attachments"):
                if fkey in claim_info and fkey != "FileList":
                    claim_info["FileList"] = claim_info.pop(fkey)

            # 计算拒赔金额
            final_status: str = str(claim.get("Final_Status") or claim.get("final_status") or "")
            if "拒赔" in final_status or "部分" in final_status:
                try:
                    apply_amount = float(claim.get("Amount") or claim.get("amount") or 0)
                    approved_amount = float(claim.get("Reserved_Amount") or claim.get("reserved_amount") or 0)
                    rejected_amount = apply_amount - approved_amount
                    claim_info["Rejected_Amount"] = f"{rejected_amount:.2f}"
                except (ValueError, TypeError):
                    pass

            case_dir.mkdir(parents=True, exist_ok=True)
            with open(claim_info_path, "w", encoding="utf-8") as f:
                json.dump(claim_info, f, ensure_ascii=False, indent=4)

            # ---------- 更新进度记录 ----------
            protected_keys = {"totalFiles", "downloadedFiles", "failedFiles", "status", "startTime", "completedTime"}
            for k, v in claim.items():
                if k.lower() in ("files", "attachments"):
                    continue
                progress_key = k[0].lower() + k[1:] if k else k
                if progress_key not in protected_keys:
                    existing_record[progress_key] = v

            files: List[Dict] = (
                claim.get("FileList")
                or claim.get("Files")
                or claim.get("files")
                or claim.get("Attachments")
                or claim.get("attachments")
                or []
            )

            existing_record["fileList"] = files
            existing_record.setdefault("applicantName", applicant_name)
            existing_record.setdefault("benefitName", benefit_name)
            existing_record.setdefault("totalFiles", len(files))
            existing_record.setdefault("downloadedFiles", [])
            existing_record.setdefault("failedFiles", [])
            existing_record.setdefault("status", "pending")
            existing_record.setdefault("startTime", datetime.now().isoformat())
            existing_record["totalFiles"] = len(files)

            # ---------- 确定需下载文件 ----------
            downloaded_files: List[str] = existing_record["downloadedFiles"]
            failed_files: List[str] = existing_record["failedFiles"]

            files_to_download = [
                f for f in files
                if not self._is_file_downloaded(case_no, self._get_file_id(f), case_dir)
            ]

            if not files_to_download:
                LOGGER.info(f"[完成] {benefit_name}-{case_no}（{applicant_name}）文件已全部下载，更新字段")
                existing_record["status"] = "completed"
                self.progress[case_no] = existing_record
                self._save_progress()
                return

            if existing_record["status"] == "completed":
                LOGGER.info(f"[新增文件] {benefit_name}-{case_no}（{applicant_name}）发现 {len(files_to_download)} 个未下载文件")
            else:
                LOGGER.info(f"[下载中] {benefit_name}-{case_no}（{applicant_name}）共 {len(files)} 个文件，需下载 {len(files_to_download)} 个")

            existing_record["status"] = "in_progress"
            self.progress[case_no] = existing_record

            # ---------- 并发下载 ----------
            newly_failed: List[str] = []
            newly_downloaded: List[str] = []

            # 并发下载文件
            download_tasks = []
            for file_info in files_to_download:
                file_id = self._get_file_id(file_info)
                file_url = self._get_file_url(file_info)
                file_name = self._get_file_name(file_info)

                if not file_url:
                    LOGGER.warning(f"    [跳过] 文件无 URL: {file_info}")
                    continue

                dest_name = _sanitize_filename(file_name or file_id)
                dest_path = case_dir / dest_name

                LOGGER.info(f"    ↓ {dest_name[:60]}{'...' if len(dest_name) > 60 else ''}")
                download_tasks.append((file_id, file_url, dest_path))

            # 并发执行下载（限制并发数）
            async def _do_download(fid: str, furl: str, dpath: Path):
                success = await self._download_file_async(furl, dpath)
                return fid, success, dpath

            sem = asyncio.Semaphore(self.max_concurrent)
            tasks = [_do_download(fid, furl, dpath) for fid, furl, dpath in download_tasks]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    newly_failed.append("unknown")
                    continue
                fid, success, dpath = result
                if success:
                    actual_name = self._find_actual_filename(case_dir, Path(dpath).stem)
                    newly_downloaded.append(actual_name or dpath.name)
                    failed_files = [f for f in failed_files if Path(f).stem != Path(fid).stem]
                else:
                    newly_failed.append(fid)

                await asyncio.sleep(0.1)  # 限速保护

            # ---------- 更新进度 ----------
            # 合并后去重（以 stem 为唯一键），防止补件重新下载时文件名重复累积
            merged = {Path(f).stem: f for f in downloaded_files}
            for f in newly_downloaded:
                merged[Path(f).stem] = f
            existing_record["downloadedFiles"] = list(merged.values())
            existing_record["failedFiles"] = list(set(failed_files + newly_failed))

            all_done = len(existing_record["downloadedFiles"]) >= len(files) and not existing_record["failedFiles"]
            existing_record["status"] = "completed" if all_done else "partial"
            if all_done:
                existing_record["completedTime"] = datetime.now().isoformat()

            self.progress[case_no] = existing_record
            self._save_progress()

            status_icon = "✓" if all_done else "⚠"
            LOGGER.info(f"  {status_icon} {case_no}: 本次下载 {len(newly_downloaded)}，失败 {len(newly_failed)}，累计 {len(existing_record['downloadedFiles'])}/{len(files)}")

    # ---------- 主流程 ---------- #
    async def run_async(self, payload: dict) -> None:
        """异步拉取并批量下载"""
        LOGGER.info(f"[开始] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 拉取理赔数据...")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        claims = await self.fetch_claims(payload)
        if not claims:
            LOGGER.info("[结束] 未获取到任何理赔记录")
            return

        LOGGER.info(f"[获取] 共 {len(claims)} 条理赔记录")

        semaphore = asyncio.Semaphore(self.max_concurrent)
        for idx, claim in enumerate(claims, 1):
            case_no = claim.get("CaseNo") or claim.get("caseNo") or "?"
            LOGGER.info(f"[{idx}/{len(claims)}] 处理案件 {case_no}")
            await self._process_claim_async(claim, semaphore)
            await asyncio.sleep(0.5)  # 案件间稍作停顿，避免对API压力过大

        # 汇总
        total = len(self.progress)
        completed = sum(1 for v in self.progress.values() if v.get("status") == "completed")
        partial = sum(1 for v in self.progress.values() if v.get("status") == "partial")
        LOGGER.info(f"{'='*50}")
        LOGGER.info(f"[汇总] 总案件: {total}，完成: {completed}，部分失败: {partial}")
        LOGGER.info(f"[结束] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ---------- 辅助方法（复用） ---------- #
    def _get_case_dir(self, benefit_name: str, case_no: str) -> Path:
        return self.output_dir / benefit_name / f"{benefit_name}-案件号【{case_no}】"

    @staticmethod
    def _get_file_id(file_info: Dict) -> str:
        explicit = (
            file_info.get("FileId") or file_info.get("fileId") or
            file_info.get("Id") or file_info.get("id") or ""
        ).strip()
        if explicit:
            return explicit
        # 重要：需要解码以匹配磁盘上的文件名（_get_file_name 会解码）
        url = (
            file_info.get("FileUrl") or file_info.get("fileUrl") or
            file_info.get("url") or ""
        ).strip()
        if url:
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if path:
                return unquote(path.split("/")[-1])
        return ""

    @staticmethod
    def _get_file_url(file_info: Dict) -> str:
        return str(
            file_info.get("Url") or file_info.get("url") or
            file_info.get("FileUrl") or file_info.get("fileUrl") or
            file_info.get("FileURL") or file_info.get("fileURL") or
            file_info.get("DownloadUrl") or file_info.get("downloadUrl") or ""
        ).strip()

    @staticmethod
    def _get_file_name(file_info: Dict) -> str:
        name = str(
            file_info.get("FileName") or file_info.get("fileName") or
            file_info.get("Name") or file_info.get("name") or ""
        ).strip()
        if name:
            return name

        url = AsyncClaimDownloader._get_file_url(file_info)
        if url:
            from urllib.parse import urlparse, parse_qs, unquote
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if path:
                fname = path.split("/")[-1]
                if fname and "." in fname:
                    return unquote(fname)

            if "filebridge.alipay.com" in parsed.netloc:
                params = parse_qs(parsed.query)
                file_keys = params.get("fileKey", [])
                if file_keys:
                    fk = file_keys[0].rstrip("/")
                    if fk:
                        return unquote(fk.split("/")[-1])

            for key in ("filename", "name", "file"):
                vals = params.get(key, []) if 'params' in locals() else []
                if vals and vals[0]:
                    return unquote(vals[0])

        return ""

    @staticmethod
    def _find_actual_filename(folder: Path, stem: str) -> Optional[str]:
        if not folder.exists():
            return None
        for f in folder.iterdir():
            if f.stem == stem:
                return f.name
        return None

    def _is_file_downloaded(self, case_no: str, file_id: str, case_dir: Path) -> bool:
        record = self.progress.get(case_no, {})
        downloaded = record.get("downloadedFiles", [])
        file_stem = Path(file_id).stem
        if not any(Path(f).stem == file_stem for f in downloaded):
            return False
        if not case_dir.exists():
            return False
        for f in case_dir.iterdir():
            if f.is_file() and f.stem == file_stem:
                return True
        return False


# ------------------------------------------------------------------ #
#  脚本入口（保持兼容）
# ------------------------------------------------------------------ #


def _sanitize_filename(name: str) -> str:
    """替换 Windows 文件名中的非法字符（\ / : * ? " < > |）为下划线。"""
    import re
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def _safe_float_dl(val):
    try:
        return float(val) if val is not None and val != "" else None
    except (ValueError, TypeError):
        return None


def _parse_date_dl(val: str):
    """将 'YYYYMMDDHHMMSS' 或 'YYYY-MM-DD' 等格式转为 date，失败返回 None"""
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).date()
        except Exception:
            continue
    return None


def _save_claim_info_to_db(claim_info: dict):
    """将 claim_info.json 关键字段同步写入 ai_claim_info_raw 表。
    download_claims.py 是同步脚本，用 asyncio.run 包裹异步 upsert。"""
    import asyncio
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db.database import DatabaseConnection, ClaimInfoRawDAO
    from app.db.models import ClaimInfoRaw

    # 从 samePolicyClaim[0] 提取 PascalCase 字段（被保险人维度）
    spc_list = claim_info.get("samePolicyClaim") or []
    spc = spc_list[0] if spc_list else {}

    def _g(d, *keys):
        """取多个候选键中第一个非空值"""
        for k in keys:
            v = d.get(k)
            if v is not None and v != "":
                return v
        return None

    record = ClaimInfoRaw(
        forceid=str(claim_info.get("forceid") or ""),
        claim_id=str(_g(claim_info, "claimId", "ClaimId") or ""),

        benefit_name=_g(claim_info, "benefitName", "BenefitName"),
        applicant_name=_g(claim_info, "applicant_Name", "applicantName", "Applicant_Name"),

        # 来自 samePolicyClaim 的被保险人信息
        insured_name=_g(spc, "Insured_And_Policy"),
        id_type=_g(spc, "ID_Type"),
        id_number=_g(spc, "ID_Number"),
        birthday=_parse_date_dl(_g(spc, "Birthday")),
        gender=_g(spc, "Gender"),
        policy_no=_g(spc, "PolicyNo"),
        insurance_company=_g(spc, "Insurance_Company"),
        product_name=_g(spc, "Product_Name"),
        plan_name=_g(spc, "Plan_Name"),
        effective_date=_g(spc, "Effective_Date"),
        expiry_date=_g(spc, "Expiry_Date"),
        date_of_insurance=_g(spc, "Date_of_Insurance"),

        # 本案维度（camelCase）
        case_insured_name=_g(claim_info, "insured_And_Policy", "insuredAndPolicy"),
        case_policy_no=_g(claim_info, "policyNo", "PolicyNo"),
        case_insurance_company=_g(claim_info, "insurance_Company", "Insurance_Company"),
        case_effective_date=_g(claim_info, "effective_Date", "Effective_Date"),
        case_expiry_date=_g(claim_info, "expiry_Date", "Expiry_Date"),
        case_id_type=_g(claim_info, "iD_Type", "ID_Type"),
        case_id_number=_g(claim_info, "iD_Number", "ID_Number"),
        insured_amount=_safe_float_dl(_g(claim_info, "insured_Amount", "Insured_Amount")),
        reserved_amount=_safe_float_dl(_g(claim_info, "reserved_Amount", "Reserved_Amount")),
        remaining_coverage=_safe_float_dl(_g(claim_info, "remaining_Coverage", "Remaining_Coverage")),
        claim_amount=_safe_float_dl(_g(claim_info, "amount", "Amount")),

        date_of_accident=_parse_date_dl(_g(claim_info, "date_of_Accident", "Date_of_Accident")),
        final_status=_g(claim_info, "final_Status", "Final_Status"),
        description_of_accident=_g(claim_info, "description_of_Accident", "Description_of_Accident"),

        source_date=_g(spc, "Source_Date") or _g(claim_info, "source_Date"),

        raw_json=json.dumps(claim_info, ensure_ascii=False),
    )

    async def _run():
        db = DatabaseConnection()
        await db.initialize()
        try:
            dao = ClaimInfoRawDAO(db)
            await dao.upsert(record)
        finally:
            await db.close()

    asyncio.run(_run())


if __name__ == "__main__":
    # 兼容性：直接运行脚本时使用同步版本
    API_URL = "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim"
    REQUEST_PAYLOAD = {}

    downloader = ClaimDownloader(
        api_url=API_URL,
        output_dir="claims_data",
        force_refresh=True,
    )
    downloader.run(REQUEST_PAYLOAD)


# ====================================================================== #
#  供 download_scheduler.py 调用的异步辅助函数
# ====================================================================== #

async def run_download_async(
    api_url: str,
    payload: dict,
    output_dir: Path,
    force_refresh: bool = False,
    max_concurrent: int = 10,
) -> Tuple[int, str]:
    """
    供外部调度器调用的异步下载入口
    Returns: (下载完成案件数, 消息)
    """
    global _async_session
    _async_session = aiohttp.ClientSession(trust_env=True)

    downloader = AsyncClaimDownloader(
        api_url=api_url,
        output_dir=str(output_dir),
        force_refresh=force_refresh,
        max_concurrent_downloads=max_concurrent,
    )

    try:
        await downloader.run_async(payload)
        # 统计完成案件数
        completed = sum(1 for v in downloader.progress.values() if v.get("status") == "completed")
        return completed, f"异步下载完成，共 {completed} 个案件"
    except Exception as e:
        LOGGER.error(f"异步下载异常: {e}")
        return 0, f"下载异常: {e}"
    finally:
        await _async_session.close()
        _async_session = None


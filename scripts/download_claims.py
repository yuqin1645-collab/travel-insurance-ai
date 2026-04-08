#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理赔材料下载工具
支持断点续传、自动文件类型检测、结构化存储
"""

import os
import json
import requests
from pathlib import Path
from typing import Dict, List, Optional
import time
from datetime import datetime


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
                print(f"[警告] 进度文件读取失败，将从头开始: {e}")
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
                print(f"[错误] API 请求失败 (第{page_index}页): {e}")
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
                        print(f"[信息] API 返回单条记录 dict，已按单条处理")
                    else:
                        print(f"[警告] 未识别的 API 返回格式: {type(data)}，keys={list(data.keys())[:10]}")
                        break

            all_claims.extend(records)

            # 打印分页进度
            if total_count is not None:
                print(f"  第{page_index}/{total_page or '?'}页，本页 {len(records)} 条，累计 {len(all_claims)}/{total_count}")
            else:
                print(f"  第{page_index}页，本页 {len(records)} 条，累计 {len(all_claims)}")

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
        """下载单个文件到 dest_path，自动补全无后缀文件的扩展名。
        返回 True 表示成功，False 表示失败。"""
        try:
            resp = requests.get(url, timeout=120, stream=True)
            resp.raise_for_status()

            # 读取全部内容（理赔材料一般不会太大���
            content = resp.content

            # 如果目标路径没有后缀，自动检测并追加
            if not dest_path.suffix:
                ext = detect_extension(content)
                dest_path = dest_path.with_suffix(ext)

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(content)
            return True

        except requests.RequestException as e:
            print(f"    [失败] 下载 {url} → {e}")
            return False
        except IOError as e:
            print(f"    [失败] 写入文件 {dest_path} → {e}")
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
            print(f"[跳过] 缺少 CaseNo 或 BenefitName: {claim}")
            return

        # 获取文件列表，兼容多种字段名
        files: List[Dict] = (
            claim.get("Files")
            or claim.get("files")
            or claim.get("Attachments")
            or claim.get("attachments")
            or []
        )

        case_dir = self._get_case_dir(benefit_name, case_no)
        existing_record = self.progress.get(case_no, {})

        # force_refresh 模式：仅删除旧 claim_info.json，附件文件不重新下载
        if self.force_refresh:
            claim_info_path_old = case_dir / "claim_info.json"
            if claim_info_path_old.exists():
                claim_info_path_old.unlink()

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
            print(f"[已完成] {benefit_name}-{case_no}（{applicant_name}）文件已全部下载，更新字段")
            existing_record["status"] = "completed"
            self.progress[case_no] = existing_record
            self._save_progress()
            return

        # 有新文件（或失败重试）需要下载
        if existing_record["status"] == "completed":
            print(f"[新增文件] {benefit_name}-{case_no}（{applicant_name}）发现 {len(files_to_download)} 个未下载文件")
        else:
            print(f"[下载中] {benefit_name}-{case_no}（{applicant_name}）共 {len(files)} 个文件，"
                  f"需下载 {len(files_to_download)} 个")

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
                print(f"    [跳过] 文件无 URL: {file_info}")
                continue

            # 确定保存路径（无后缀时先用 file_id，下载后再补扩展名）
            dest_name = file_name or file_id
            dest_path = case_dir / dest_name

            print(f"    ↓ {dest_name[:60]}{'...' if len(dest_name) > 60 else ''}")

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
        existing_record["downloadedFiles"] = downloaded_files + newly_downloaded
        existing_record["failedFiles"] = list(set(failed_files + newly_failed))

        all_done = len(existing_record["downloadedFiles"]) >= len(files) and not existing_record["failedFiles"]
        existing_record["status"] = "completed" if all_done else "partial"
        if all_done:
            existing_record["completedTime"] = datetime.now().isoformat()

        self.progress[case_no] = existing_record
        self._save_progress()

        status_icon = "✓" if all_done else "⚠"
        print(f"  {status_icon} {case_no}: 本次下载 {len(newly_downloaded)}，"
              f"失败 {len(newly_failed)}，累计 {len(existing_record['downloadedFiles'])}/{len(files)}")

    # ------------------------------------------------------------------ #
    #  辅助方法：从文件信息 dict 中提取字段（兼容多种字段名）
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_file_id(file_info: Dict) -> str:
        return str(
            file_info.get("FileId") or file_info.get("fileId") or
            file_info.get("Id") or file_info.get("id") or
            file_info.get("FileName") or file_info.get("fileName") or ""
        ).strip()

    @staticmethod
    def _get_file_url(file_info: Dict) -> str:
        return str(
            file_info.get("Url") or file_info.get("url") or
            file_info.get("FileUrl") or file_info.get("fileUrl") or
            file_info.get("DownloadUrl") or file_info.get("downloadUrl") or ""
        ).strip()

    @staticmethod
    def _get_file_name(file_info: Dict) -> str:
        return str(
            file_info.get("FileName") or file_info.get("fileName") or
            file_info.get("Name") or file_info.get("name") or ""
        ).strip()

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
        print(f"[开始] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 拉取理赔数据...")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        claims = self.fetch_claims(payload)
        if not claims:
            print("[结束] 未获取到任何理赔记录")
            return

        print(f"[获取] 共 {len(claims)} 条理赔记录\n")

        for idx, claim in enumerate(claims, 1):
            case_no = claim.get("CaseNo") or claim.get("caseNo") or "?"
            print(f"[{idx}/{len(claims)}] 处理案件 {case_no}")
            self.process_claim(claim)
            print()

        # 汇总
        total = len(self.progress)
        completed = sum(1 for v in self.progress.values() if v.get("status") == "completed")
        partial = sum(1 for v in self.progress.values() if v.get("status") == "partial")
        print(f"\n{'='*50}")
        print(f"[汇总] 总案件: {total}，完成: {completed}，部分失败: {partial}")
        print(f"[结束] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ------------------------------------------------------------------ #
#  脚本入口
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    API_URL = "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim"

    # POST 请求体，根据实际接口需要调整
    REQUEST_PAYLOAD = {
        # 示例：如果接口需要分页或筛选参数，在此配置
        # "pageSize": 200,
        # "pageNum": 1,
    }

    downloader = ClaimDownloader(
        api_url=API_URL,
        output_dir="claims_data",
        force_refresh=False,  # 改为 True 可强制重新下载所有文件并刷新 claim_info.json
    )
    downloader.run(REQUEST_PAYLOAD)

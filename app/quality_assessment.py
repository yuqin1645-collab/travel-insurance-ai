#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审核质量评估模块
评估AI审核的准确性和质量
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


class QualityAssessment:
    """审核质量评估器"""
    
    def __init__(self):
        self.metrics = {
            'total_reviews': 0,
            'correct_decisions': 0,
            'incorrect_decisions': 0,
            'manual_reviews': 0,
            'accuracy_rate': 0.0,
            'by_checkpoint': {}
        }
    
    def assess_review(
        self, 
        ai_result: Dict, 
        ground_truth: Optional[Dict] = None
    ) -> Dict:
        """
        评估单个审核结果
        
        Args:
            ai_result: AI审核结果
            ground_truth: 人工审核的标准答案(可选)
        
        Returns:
            评估报告
        """
        assessment = {
            'forceid': ai_result.get('forceid'),
            'timestamp': datetime.now().isoformat(),
            'completeness_score': 0.0,
            'consistency_score': 0.0,
            'logic_score': 0.0,
            'overall_score': 0.0,
            'issues': [],
            'suggestions': []
        }
        
        # 1. 完整性检查
        completeness = self._check_completeness(ai_result)
        assessment['completeness_score'] = completeness['score']
        assessment['issues'].extend(completeness['issues'])
        
        # 2. 一致性检查
        consistency = self._check_consistency(ai_result)
        assessment['consistency_score'] = consistency['score']
        assessment['issues'].extend(consistency['issues'])
        
        # 3. 逻辑性检查
        logic = self._check_logic(ai_result)
        assessment['logic_score'] = logic['score']
        assessment['issues'].extend(logic['issues'])
        
        # 4. 如果有标准答案,进行准确性对比
        if ground_truth:
            accuracy = self._check_accuracy(ai_result, ground_truth)
            assessment['accuracy_score'] = accuracy['score']
            assessment['issues'].extend(accuracy['issues'])
            assessment['overall_score'] = (
                completeness['score'] * 0.2 +
                consistency['score'] * 0.2 +
                logic['score'] * 0.2 +
                accuracy['score'] * 0.4
            )
        else:
            assessment['overall_score'] = (
                completeness['score'] * 0.4 +
                consistency['score'] * 0.3 +
                logic['score'] * 0.3
            )
        
        # 5. 生成改进建议
        assessment['suggestions'] = self._generate_suggestions(assessment)
        
        return assessment
    
    def _check_completeness(self, result: Dict) -> Dict:
        """检查结果完整性"""
        issues = []
        score = 100.0
        
        # 检查必需字段
        required_fields = ['forceid', 'Remark', 'IsAdditional', 'KeyConclusions']
        for field in required_fields:
            if field not in result or not result[field]:
                issues.append(f"缺少必需字段: {field}")
                score -= 25
        
        # 检查KeyConclusions
        if 'KeyConclusions' in result:
            conclusions = result['KeyConclusions']
            expected_checkpoints = [
                '保障责任核对',
                '材料完整性核对',
                '保障范围核对',
                '除外责任核对',
                '赔偿金额核对'
            ]
            
            actual_checkpoints = [c.get('checkpoint') for c in conclusions]
            for checkpoint in expected_checkpoints:
                if checkpoint not in actual_checkpoints:
                    issues.append(f"缺少核对点: {checkpoint}")
                    score -= 10
        
        return {
            'score': max(0, score),
            'issues': issues
        }
    
    def _check_consistency(self, result: Dict) -> Dict:
        """检查结果一致性"""
        issues = []
        score = 100.0
        
        remark = result.get('Remark', '')
        is_additional = result.get('IsAdditional', '')
        conclusions = result.get('KeyConclusions', [])
        
        # 检查IsAdditional与Remark的一致性
        if is_additional == 'Y':
            if '补充材料' not in remark and '补件' not in remark and '人工审核' not in remark:
                issues.append("IsAdditional为Y但Remark未说明需要补件")
                score -= 20
        
        # 检查拒赔与核对点的一致性
        if '拒赔' in remark:
            # 应该至少有一个核对点不满足
            eligible_list = [c.get('Eligible') for c in conclusions]
            if 'N' not in eligible_list:
                issues.append("Remark显示拒赔但所有核对点都满足")
                score -= 30
        
        # 检查赔付与核对点的一致性
        if '赔付' in remark and '拒赔' not in remark:
            # 所有核对点应该都满足
            eligible_list = [c.get('Eligible') for c in conclusions]
            if 'N' in eligible_list:
                issues.append("Remark显示赔付但有核对点不满足")
                score -= 30
        
        return {
            'score': max(0, score),
            'issues': issues
        }
    
    def _check_logic(self, result: Dict) -> Dict:
        """检查逻辑合理性"""
        issues = []
        score = 100.0
        
        conclusions = result.get('KeyConclusions', [])
        
        # 检查审核顺序逻辑
        # 如果保障责任不满足,后续核对应该也不满足或不适用
        coverage_eligible = None
        material_eligible = None
        
        for c in conclusions:
            checkpoint = c.get('checkpoint', '')
            eligible = c.get('Eligible', '')
            
            if '保障责任' in checkpoint:
                coverage_eligible = eligible
            elif '材料完整性' in checkpoint:
                material_eligible = eligible
        
        # 如果保障责任不满足,不应该继续审核赔偿金额
        if coverage_eligible == 'N':
            for c in conclusions:
                if '赔偿金额' in c.get('checkpoint', ''):
                    if c.get('Eligible') == 'Y':
                        issues.append("保障责任不满足但赔偿金额核对通过,逻辑矛盾")
                        score -= 25
        
        # 如果材料不完整,应该要求补件
        if material_eligible == 'N':
            if result.get('IsAdditional') != 'Y':
                issues.append("材料不完整但未要求补件")
                score -= 20
        
        return {
            'score': max(0, score),
            'issues': issues
        }
    
    def _check_accuracy(self, ai_result: Dict, ground_truth: Dict) -> Dict:
        """对比标准答案检查准确性"""
        issues = []
        score = 100.0
        
        # 对比最终决策
        ai_decision = ai_result.get('Remark', '')
        gt_decision = ground_truth.get('Remark', '')
        
        # 简化对比:是否都是赔付/拒赔/补件
        ai_type = self._classify_decision(ai_decision)
        gt_type = self._classify_decision(gt_decision)
        
        if ai_type != gt_type:
            issues.append(f"决策类型不一致: AI={ai_type}, 标准={gt_type}")
            score -= 40
        
        # 对比各个核对点
        ai_conclusions = {c['checkpoint']: c for c in ai_result.get('KeyConclusions', [])}
        gt_conclusions = {c['checkpoint']: c for c in ground_truth.get('KeyConclusions', [])}
        
        for checkpoint in gt_conclusions:
            if checkpoint not in ai_conclusions:
                issues.append(f"缺少核对点: {checkpoint}")
                score -= 10
            elif ai_conclusions[checkpoint]['Eligible'] != gt_conclusions[checkpoint]['Eligible']:
                issues.append(f"核对点判断不一致: {checkpoint}")
                score -= 10
        
        return {
            'score': max(0, score),
            'issues': issues
        }
    
    def _classify_decision(self, remark: str) -> str:
        """分类决策类型"""
        if '补充材料' in remark or '补件' in remark or '人工审核' in remark:
            return 'additional'
        elif '拒赔' in remark:
            return 'reject'
        elif '赔付' in remark or '同意' in remark:
            return 'approve'
        else:
            return 'unknown'
    
    def _generate_suggestions(self, assessment: Dict) -> List[str]:
        """生成改进建议"""
        suggestions = []
        
        if assessment['completeness_score'] < 80:
            suggestions.append("建议完善审核结果的必需字段和核对点")
        
        if assessment['consistency_score'] < 80:
            suggestions.append("建议检查审核结论与核对点的一致性")
        
        if assessment['logic_score'] < 80:
            suggestions.append("建议优化审核逻辑,确保各阶段判断合理")
        
        if 'accuracy_score' in assessment and assessment['accuracy_score'] < 80:
            suggestions.append("建议对比人工审核结果,优化prompt和审核规则")
        
        return suggestions
    
    def batch_assess(
        self, 
        results_dir: Path,
        ground_truth_dir: Optional[Path] = None
    ) -> Dict:
        """
        批量评估审核结果
        
        Args:
            results_dir: AI审核结果目录
            ground_truth_dir: 人工审核标准答案目录(可选)
        
        Returns:
            批量评估报告
        """
        assessments = []
        
        # 读取所有AI审核结果
        result_files = list(results_dir.glob('*_ai_review.json'))
        
        for result_file in result_files:
            with open(result_file, 'r', encoding='utf-8') as f:
                ai_result = json.load(f)
            
            # 查找对应的标准答案
            ground_truth = None
            if ground_truth_dir:
                gt_file = ground_truth_dir / result_file.name.replace('_ai_review', '_manual_review')
                if gt_file.exists():
                    with open(gt_file, 'r', encoding='utf-8') as f:
                        ground_truth = json.load(f)
            
            # 评估
            assessment = self.assess_review(ai_result, ground_truth)
            assessments.append(assessment)
        
        # 汇总统计
        summary = self._summarize_assessments(assessments)
        
        return {
            'summary': summary,
            'details': assessments
        }
    
    def _summarize_assessments(self, assessments: List[Dict]) -> Dict:
        """汇总评估结果"""
        if not assessments:
            return {}
        
        total = len(assessments)
        
        summary = {
            'total_reviews': total,
            'avg_completeness': sum(a['completeness_score'] for a in assessments) / total,
            'avg_consistency': sum(a['consistency_score'] for a in assessments) / total,
            'avg_logic': sum(a['logic_score'] for a in assessments) / total,
            'avg_overall': sum(a['overall_score'] for a in assessments) / total,
            'high_quality_count': sum(1 for a in assessments if a['overall_score'] >= 80),
            'medium_quality_count': sum(1 for a in assessments if 60 <= a['overall_score'] < 80),
            'low_quality_count': sum(1 for a in assessments if a['overall_score'] < 60),
            'common_issues': self._find_common_issues(assessments)
        }
        
        summary['high_quality_rate'] = summary['high_quality_count'] / total * 100
        
        return summary
    
    def _find_common_issues(self, assessments: List[Dict]) -> List[Dict]:
        """找出常见问题"""
        issue_counts = {}
        
        for assessment in assessments:
            for issue in assessment['issues']:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        
        # 按频率排序
        common_issues = [
            {'issue': issue, 'count': count, 'frequency': count / len(assessments) * 100}
            for issue, count in sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)
        ]
        
        return common_issues[:10]  # 返回前10个常见问题


def test_quality_assessment():
    """测试质量评估"""
    print("测试审核质量评估...")
    print("=" * 60)
    
    assessor = QualityAssessment()
    
    # 测试案例1: 完整的审核结果
    ai_result = {
        "forceid": "a0nC800000I2LwIIAV",
        "Remark": "审核通过,同意赔付1000元",
        "IsAdditional": "N",
        "KeyConclusions": [
            {"checkpoint": "保障责任核对", "Eligible": "Y", "Remark": "符合"},
            {"checkpoint": "材料完整性核对", "Eligible": "Y", "Remark": "齐全"},
            {"checkpoint": "保障范围核对", "Eligible": "Y", "Remark": "属于"},
            {"checkpoint": "除外责任核对", "Eligible": "Y", "Remark": "未触发"},
            {"checkpoint": "赔偿金额核对", "Eligible": "Y", "Remark": "1000元"}
        ]
    }
    
    assessment = assessor.assess_review(ai_result)
    
    print("\n测试案例1: 完整的审核结果")
    print(f"完整性得分: {assessment['completeness_score']}")
    print(f"一致性得分: {assessment['consistency_score']}")
    print(f"逻辑性得分: {assessment['logic_score']}")
    print(f"综合得分: {assessment['overall_score']}")
    print(f"问题: {assessment['issues']}")
    print(f"建议: {assessment['suggestions']}")
    
    # 测试案例2: 不一致的审核结果
    ai_result2 = {
        "forceid": "a0nC800000I2McgIAF",
        "Remark": "拒赔: 不符合保障责任",
        "IsAdditional": "N",
        "KeyConclusions": [
            {"checkpoint": "保障责任核对", "Eligible": "Y", "Remark": "符合"},  # 矛盾
            {"checkpoint": "材料完整性核对", "Eligible": "Y", "Remark": "齐全"}
        ]
    }
    
    assessment2 = assessor.assess_review(ai_result2)
    
    print("\n" + "=" * 60)
    print("测试案例2: 不一致的审核结果")
    print(f"完整性得分: {assessment2['completeness_score']}")
    print(f"一致性得分: {assessment2['consistency_score']}")
    print(f"逻辑性得分: {assessment2['logic_score']}")
    print(f"综合得分: {assessment2['overall_score']}")
    print(f"问题: {assessment2['issues']}")
    print(f"建议: {assessment2['suggestions']}")
    
    print("\n" + "=" * 60)
    print("测试完成!")


if __name__ == "__main__":
    test_quality_assessment()

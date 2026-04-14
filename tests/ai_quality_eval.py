import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

load_dotenv()

from src.core.ai.evaluator import build_case_map, evaluate_requirements
from src.data.database import get_all_requirements, get_all_test_cases, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 测试用例质量评估脚本")
    parser.add_argument("--req-id", help="仅评估指定需求 ID")
    parser.add_argument("--limit", type=int, default=0, help="最多评估多少条需求，0 表示不限制")
    parser.add_argument(
        "--output",
        default=str(project_root / "data" / "ai_quality_report.json"),
        help="评估报告输出路径",
    )
    args = parser.parse_args()

    init_db()
    all_requirements = get_all_requirements()
    all_cases = get_all_test_cases()
    case_map = build_case_map(all_cases)

    requirements = [req for req in all_requirements if case_map.get(req.id)]
    if args.req_id:
        requirements = [req for req in requirements if req.id == args.req_id]
    if args.limit and args.limit > 0:
        requirements = requirements[: args.limit]

    if not requirements:
        print("没有可评估的需求/用例。")
        return 1

    report = asyncio.run(evaluate_requirements(requirements, case_map))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report["summary"]
    print("=== AI 质量评估完成 ===")
    print(f"评估需求数: {summary['requirements_evaluated']}")
    print(f"平均得分: {summary['average_score']}")
    print(f"通过数: {summary['passed_count']} | 未通过数: {summary['failed_count']}")
    print(f"总 Tokens: {summary['total_tokens']}")
    print(f"报告文件: {output_path}")

    low_score_items = sorted(report["items"], key=lambda x: x["score"])[:5]
    if low_score_items:
        print("=== 低分需求 Top 5 ===")
        for item in low_score_items:
            print(
                f"{item['req_id']} | score={item['score']} | "
                f"violations={len(item['violations'])} | gaps={len(item['gaps'])}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

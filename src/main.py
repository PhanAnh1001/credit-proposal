#!/usr/bin/env python3
"""
Credit Proposal AI Agent - Main Entry Point

Usage:
    python -m src.main
    python -m src.main --company mst
"""
import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables before importing config (config reads env vars at import time)
load_dotenv(".env")

from src.config import UPLOADS_DIR, get_financial_statements_dir, get_general_info_path, get_output_dir  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Credit Proposal AI Agent')
    parser.add_argument(
        '--company',
        type=str,
        default='mst',
        help='Company code (default: mst)'
    )
    parser.add_argument(
        '--company-name',
        type=str,
        default='Công ty Cổ phần Xây dựng MST',
        help='Full company name'
    )
    parser.add_argument(
        '--base-dir',
        type=str,
        default=None,
        help='Base uploads directory (default: data/uploads)'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory (default: data/outputs/<company>)'
    )

    args = parser.parse_args()

    company = args.company.lower()

    if args.base_dir:
        base_dir = Path(args.base_dir) / company
        md_path = str(base_dir / "general-information" / "md" / f"{company}-information.md")
        pdf_dir = str(base_dir / "financial-statements" / "pdf")
    else:
        md_path = str(get_general_info_path(company))
        pdf_dir = str(get_financial_statements_dir(company))

    output_dir = args.output_dir or str(get_output_dir(company))

    # Validate inputs
    if not Path(md_path).exists():
        print(f"ERROR: Company info file not found: {md_path}")
        sys.exit(1)

    if not Path(pdf_dir).exists():
        print(f"ERROR: PDF directory not found: {pdf_dir}")
        sys.exit(1)

    print(f"Company: {args.company_name}")
    print(f"MD file: {md_path}")
    print(f"PDF dir: {pdf_dir}")
    print(f"Output:  {output_dir}")
    print()

    # Run the agent
    from src.agents.graph import run_credit_proposal

    result = run_credit_proposal(
        company=company,
        company_name=args.company_name,
        md_company_info_path=md_path,
        pdf_dir_path=pdf_dir,
        output_dir=output_dir,
    )

    if result.get('errors'):
        print(f"\nCompleted with {len(result['errors'])} error(s):")
        for err in result['errors']:
            print(f"  - {err}")
    else:
        print("\nCompleted successfully!")

    md_output = result.get('final_report_md')
    if md_output:
        print(f"\nReport preview (first 500 chars):\n{'='*40}")
        print(md_output[:500])
        print(f"{'='*40}")

    return 0 if not result.get('errors') else 1


if __name__ == '__main__':
    sys.exit(main())

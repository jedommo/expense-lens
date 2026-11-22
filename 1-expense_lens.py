#!/usr/bin/env python3
"""Summarize single-currency expense CSVs, offline and without dependencies."""
import argparse
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from html import escape
import json
from pathlib import Path
import re
import sys

MAX_AMOUNT = Decimal('999999999999.99')


@dataclass(frozen=True)
class Expense:
    date: date
    category: str
    amount: Decimal


def read_expenses(path):
    """Validate the entire input. Never silently skip malformed records."""
    expenses = []
    with Path(path).open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source, strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError('Duplicate CSV headers are not allowed.')
        if not {'date', 'category', 'amount'} <= set(headers):
            raise ValueError('Required CSV headers: date,category,amount')
        for row in reader:
            line = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Line {line}: wrong number of CSV fields.')
            raw_date = row['date'].strip()
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', raw_date):
                raise ValueError(f'Line {line}: date must be YYYY-MM-DD.')
            try:
                day = date.fromisoformat(raw_date)
            except ValueError:
                raise ValueError(f'Line {line}: invalid calendar date.') from None
            category = row['category'].strip()
            if not category or len(category) > 120:
                raise ValueError(f'Line {line}: category needs 1-120 characters.')
            raw_amount = row['amount'].strip()
            if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', raw_amount):
                raise ValueError(f'Line {line}: amount must be non-negative with at most 2 decimals.')
            try:
                amount = Decimal(raw_amount)
            except InvalidOperation:
                raise ValueError(f'Line {line}: invalid amount.') from None
            if amount > MAX_AMOUNT:
                raise ValueError(f'Line {line}: amount exceeds {MAX_AMOUNT}.')
            expenses.append(Expense(day, category, amount))
    if not expenses:
        raise ValueError('CSV contains no expense records.')
    return expenses


def summarize(expenses, currency='SAR', month=None):
    """Return JSON-safe totals. Decimal math avoids binary float rounding."""
    if not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Currency must be a three-letter uppercase label, e.g. SAR.')
    if month is not None:
        if not re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', month) or month[:4] == '0000':
            raise ValueError('Month must be YYYY-MM.')
    chosen = [e for e in expenses if month is None or e.date.isoformat()[:7] == month]
    by_category, by_month = {}, {}
    with localcontext() as context:
        context.prec = 50
        for expense in chosen:
            key = expense.date.isoformat()[:7]
            by_category[expense.category] = by_category.get(expense.category, Decimal(0)) + expense.amount
            by_month[key] = by_month.get(key, Decimal(0)) + expense.amount
        total = sum((e.amount for e in chosen), Decimal(0))
    return {
        'currency': currency, 'month_filter': month, 'transactions': len(chosen),
        'total': f'{total:.2f}',
        'by_category': {k: f'{v:.2f}' for k, v in sorted(by_category.items(), key=lambda item: (-item[1], item[0]))},
        'by_month': {k: f'{v:.2f}' for k, v in sorted(by_month.items())},
    }


def make_html(summary):
    """Produce a self-contained report with escaped user-supplied categories."""
    currency = escape(summary['currency'])
    def table(title, values):
        rows = ''.join(f'<tr><td dir="auto">{escape(k)}</td><td>{v}</td></tr>' for k, v in values.items())
        return f'<section><h2>{title}</h2><table><thead><tr><th>Group</th><th>Amount ({currency})</th></tr></thead><tbody>{rows}</tbody></table></section>'
    scope = escape(summary['month_filter'] or 'All months')
    empty = '<p>No matching expenses.</p>' if summary['transactions'] == 0 else ''
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Expense Lens | Expense summary</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6fa;color:#172b42;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1000px;margin:auto;padding:48px 24px}}header p{{max-width:640px;color:#53657a}}
.eyebrow{{font-size:12px;letter-spacing:2px;color:#137c70;font-weight:750}}h1{{font-size:40px;line-height:1.15;margin:12px 0}}
.cards,.tables{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:28px 0}}
article,section{{background:white;border:1px solid #dbe3ed;border-radius:14px;padding:24px;min-width:0}}
.label{{font-size:14px;color:#53657a}}.number{{font-size:32px;font-weight:750;overflow-wrap:anywhere}}.unit{{font-size:16px;color:#53657a}}
h2{{font-size:20px;margin:0 0 16px}}table{{border-collapse:collapse;width:100%;font-size:14px;table-layout:fixed}}
th,td{{text-align:left;padding:12px 0;border-bottom:1px solid #e9edf3;overflow-wrap:anywhere}}
th:last-child,td:last-child{{text-align:right;width:44%;padding-left:12px;font-variant-numeric:tabular-nums}}th{{color:#53657a;font-weight:500}}
footer{{color:#53657a;font-size:13px}}@media(max-width:650px){{.cards,.tables{{grid-template-columns:1fr}}h1{{font-size:32px}}main{{padding:28px 16px}}}}
</style><main><header><div class="eyebrow">EXPENSE LENS / OFFLINE REPORT</div>
<h1>See where the money goes.</h1><p>Expense totals grouped by category and month. Scope: {scope}. Currency is a label supplied by the user, not a conversion.</p></header>
<div class="cards"><article><div class="label">Total expenses</div><div class="number">{summary['total']} <span class="unit">{currency}</span></div></article>
<article><div class="label">Transactions included</div><div class="number">{summary['transactions']}</div></article></div>{empty}
<div class="tables">{table('By category', summary['by_category'])}{table('By month', summary['by_month'])}</div>
<footer>Generated locally by Expense Lens. Not accounting, tax or financial advice. Reports may contain sensitive financial information.</footer></main></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_file', type=Path)
    parser.add_argument('--currency', default='SAR', help='Single currency label (default: SAR); no conversion')
    parser.add_argument('--month', help='Only include YYYY-MM')
    parser.add_argument('--json', dest='json_path', type=Path, help='Write JSON totals to a new file')
    parser.add_argument('--html', dest='html_path', type=Path, help='Write an offline HTML report to a new file')
    args = parser.parse_args(argv)
    targets = [p for p in (args.json_path, args.html_path) if p is not None]
    try:
        if len({p.resolve() for p in targets}) != len(targets):
            raise ValueError('Each output must have a different path.')
        if any(p.exists() for p in targets):
            raise ValueError('Output already exists. Choose a new path; existing files are never overwritten.')
        result = summarize(read_expenses(args.csv_file), args.currency, args.month)
        for target in targets:
            if not target.parent.is_dir():
                raise ValueError(f'Output directory does not exist: {target.parent}')
        if args.json_path:
            with args.json_path.open('x', encoding='utf-8') as out:
                json.dump(result, out, ensure_ascii=False, indent=2)
                out.write('\n')
        if args.html_path:
            with args.html_path.open('x', encoding='utf-8') as out:
                out.write(make_html(result))
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(f"Expense Lens | {result['transactions']} transactions | {result['total']} {result['currency']}")
    for title, key in [('By category', 'by_category'), ('By month', 'by_month')]:
        print(f'\n{title}:')
        for group, amount in result[key].items():
            print(f'  {group}: {amount} {result["currency"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

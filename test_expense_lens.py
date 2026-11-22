import contextlib
from datetime import date
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest

from expense_lens import Expense, main, make_html, read_expenses, summarize


class ExpenseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def load(self, body):
        path = self.folder / 'input.csv'
        path.write_text(body, encoding='utf-8')
        return read_expenses(path)

    def test_sample_totals(self):
        result = summarize(read_expenses(Path(__file__).with_name('sample_expenses.csv')))
        self.assertEqual((result['transactions'], result['total']), (12, '1725.00'))
        self.assertEqual(result['by_category'], {'Office': '541.25', 'Software': '536.00', 'Training': '430.00', 'Transport': '217.75'})
        self.assertEqual(result['by_month'], {'2026-09': '623.50', '2026-10': '604.75', '2026-11': '496.75'})

    def test_month_filter(self):
        records = read_expenses(Path(__file__).with_name('sample_expenses.csv'))
        self.assertEqual(summarize(records, month='2026-11')['total'], '496.75')
        self.assertEqual(summarize(records, month='2025-11')['transactions'], 0)

    def test_exact_decimal(self):
        rows = self.load('date,category,amount\n2026-01-01,A,0.10\n2026-01-02,A,0.20\n')
        self.assertEqual(summarize(rows)['total'], '0.30')

    def test_bom_arabic_and_whitespace(self):
        rows = self.load('\ufeffdate,category,amount\n2026-01-01, نقل ,0\n')
        self.assertEqual(summarize(rows)['by_category'], {'نقل': '0.00'})

    def test_invalid_amounts(self):
        for amount in ['NaN', 'Infinity', '-1', '1.001', '1e2', 'SAR 1', '', '1000000000000']:
            with self.subTest(amount=amount), self.assertRaisesRegex(ValueError, 'Line 2'):
                self.load(f'date,category,amount\n2026-01-01,A,{amount}\n')

    def test_invalid_dates(self):
        for day in ['2026-02-29', '2026-1-01', '20260101', '0000-01-01']:
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, 'Line 2'):
                self.load(f'date,category,amount\n{day},A,1\n')

    def test_leap_day(self):
        self.assertEqual(self.load('date,category,amount\n2024-02-29,A,1\n')[0].date, date(2024, 2, 29))

    def test_empty_category(self):
        with self.assertRaisesRegex(ValueError, 'category'):
            self.load('date,category,amount\n2026-01-01, ,1\n')

    def test_headers_and_empty_files(self):
        for text in ['', 'date,category,amount\n', 'date,amount\n', 'date,category,amount,amount\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)

    def test_wrong_column_count(self):
        for row in ['2026-01-01,A', '2026-01-01,A,1,extra']:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'fields'):
                self.load('date,category,amount\n' + row + '\n')

    def test_bad_filters_and_currency(self):
        for month in ['2026-13', '2026-1', '0000-01']:
            with self.subTest(month=month), self.assertRaisesRegex(ValueError, 'Month'):
                summarize([], month=month)
        with self.assertRaisesRegex(ValueError, 'Currency'):
            summarize([], currency='sar')

    def test_html_escaping(self):
        result = summarize([Expense(date(2026, 1, 1), '<script>alert(1)</script>', Decimal('1'))])
        html = make_html(result)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_cli_exports(self):
        source = Path(__file__).with_name('sample_expenses.csv')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([str(source), '--json', str(self.folder / 'out.json'), '--html', str(self.folder / 'out.html')]), 0)
        self.assertIn('1725.00 SAR', out.getvalue())
        self.assertEqual(json.loads((self.folder / 'out.json').read_text())['total'], '1725.00')
        self.assertIn('<!doctype html>', (self.folder / 'out.html').read_text())

    def test_cli_no_overwrite(self):
        output = self.folder / 'keep.txt'
        output.write_text('keep')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main([str(output), '--html', str(output)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(output.read_text(), 'keep')

    def test_cli_errors(self):
        for args in [['missing.csv'], ['sample_expenses.csv', '--month', 'bad']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(args)
            self.assertEqual(error.exception.code, 2)

    def test_same_output_rejected(self):
        target = str(self.folder / 'out')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['missing.csv', '--html', target, '--json', target])
        self.assertFalse(Path(target).exists())


if __name__ == '__main__':
    unittest.main()

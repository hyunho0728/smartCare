"""Run existing tests by level and retain case-level results without production startup."""
import argparse
import contextlib
import datetime as dt
import importlib.metadata
import json
import platform
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'app'))

UNIT_CLASSES = {
    'test_checkup.DocumentTests',
    'test_health_analysis.InputTests',
    'test_health_analysis.ProviderTests',
    'test_health_comparisons.ComparisonTests',
    'test_health_comparisons.ProviderComparisonTests',
    'test_health_trends.TrendDataTests',
}


def flatten(suite):
    for entry in suite:
        if isinstance(entry, unittest.TestSuite):
            yield from flatten(entry)
        else:
            yield entry


def level(test):
    module = type(test).__module__
    if module.endswith('_browser'):
        return 'system'
    name = f'{module}.{type(test).__name__}'
    return 'unit' if name in UNIT_CLASSES else 'integration'


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        self.started = time.perf_counter()
        self.current = {'id': test.id(), 'outcome': 'passed', 'details': []}
        super().startTest(test)

    def record(self, outcome, detail):
        self.current['outcome'] = outcome
        self.current['details'].append(detail)

    def addFailure(self, test, err):
        self.record('failed', self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.record('error', self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.record('skipped', reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.record('expected_failure', self._exc_info_to_string(err, test))
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.record('unexpected_success', 'Unexpected success')
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            outcome = 'failed' if issubclass(err[0], test.failureException) else 'error'
            self.record(outcome, f'{subtest}:\n{self._exc_info_to_string(err, test)}')
        super().addSubTest(test, subtest, err)

    def stopTest(self, test):
        self.current['duration_seconds'] = round(time.perf_counter() - self.started, 3)
        self.cases.append(self.current)
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--level', choices=('unit', 'integration', 'system'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--log', type=Path, help='Capture UTF-8 stdout and stderr')
    parser.add_argument('--case', help='Run one exact test ID within the selected level')
    args = parser.parse_args()
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open('w', encoding='utf-8') as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                return run(args)
    return run(args)


def run(args):
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    # Discovery errors must fail the run rather than silently disappear from a category.
    if unittest.defaultTestLoader.errors:
        raise RuntimeError('\n'.join(unittest.defaultTestLoader.errors))
    selected = unittest.TestSuite(test for test in flatten(suite)
                                 if level(test) == args.level
                                 and (not args.case or test.id() == args.case))
    if not selected.countTestCases():
        raise ValueError('No tests match the requested level and case')
    started_at = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(selected)
    packages = {name: importlib.metadata.version(name) for name in
                ('Flask', 'SQLAlchemy', 'google-genai', 'playwright', 'Pillow', 'pypdf')}
    data = {'level': args.level, 'case_filter': args.case, 'started_at': started_at,
            'duration_seconds': round(time.perf_counter() - started, 3),
            'python': sys.version, 'platform': platform.platform(), 'packages': packages,
            'tests_run': result.testsRun, 'successful': result.wasSuccessful(),
            'cases': result.cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.wasSuccessful() and result.testsRun else 1


if __name__ == '__main__':
    sys.exit(main())

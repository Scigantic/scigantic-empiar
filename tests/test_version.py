"""The version a package reports must be the version it was released as.

`scigantic_empiar.__version__` said 0.4.0 in the 0.4.1 release because the constant and
`pyproject.toml` are bumped by hand in two places. Anything that prints or checks the
version (a build log, a bug report, a compatibility gate) read the wrong one.
"""
import os
import re

import scigantic_empiar


def test_version_constant_matches_pyproject():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "pyproject.toml")
    with open(path, encoding="utf-8") as fh:
        declared = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)
    assert scigantic_empiar.__version__ == declared

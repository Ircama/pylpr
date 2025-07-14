#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import re
import os
import sys

import json
from urllib import request
from pkg_resources import parse_version

###########################################################################

END_OF_INTRODUCTION = '## Installation'

EPILOGUE = '''
Full information and usage details at the [PyLpr GitHub repository](https://github.com/Ircama/PyLpr).
'''

DESCRIPTION = (
    "RFC 1179 client and server toolkits and Python library"
    " for interacting with printers via LPR protocol or RAW mode,"
    " as well as a proxy/server for debugging, job capture,"
    " and protocol analysis."
),

PACKAGE_NAME = "PyLpr"

VERSIONFILE = "pylpr/__version__.py"

###########################################################################

def versions(pkg_name, site):
    url = 'https://' + site + '.python.org/pypi/' + pkg_name + '/json'
    try:
        releases = json.loads(request.urlopen(url).read())['releases']
    except Exception as e:
        print("Error while getting data from URL '" + url + "': " + e)
        return []
    return sorted(releases, key=parse_version, reverse=True)

with open("README.md", "r", encoding="utf-8") as readme:
    long_description = readme.read()

build = ''
verstrline = open(VERSIONFILE, "rt").read()
VSRE = r"^__version__ = ['\"]([^'\"]*)['\"]"
mo = re.search(VSRE, verstrline, re.M)
if mo:
    verstr = mo.group(1)
else:
    raise RuntimeError("Unable to find version string in %s." % (VERSIONFILE,))
        

setup(
    name=PACKAGE_NAME,
    version=verstr,
    description=(DESCRIPTION),
    long_description=long_description[
        :long_description.find(END_OF_INTRODUCTION)] + EPILOGUE,
    long_description_content_type="text/markdown",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: European Union Public Licence 1.2 (EUPL 1.2)",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Development Status :: 5 - Production/Stable",
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
    ],
    keywords=("shell console tkinter"),
    author="Ircama",
    url="https://github.com/Ircama/PyLpr",
    license='EUPL-1.2',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'epson-lpr-client=pylpr_toolkit.client:main',
            'epson-lpr-server=pylpr_toolkit.server:main',
        ],
    },
    install_requires=[
        'hexdump2',
        'pyyaml',
    ],
    python_requires='>3.6'
)

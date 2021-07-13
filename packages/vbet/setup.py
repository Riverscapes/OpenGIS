#!/usr/bin/env python

import re
from setuptools import setup

# https://packaging.python.org/discussions/install-requires-vs-requirements/
install_requires = [
    'termcolor', 'Cython==0.29.23', 'numpy>=1.21.0', 'scipy>=1.5.1',
    'argparse', 'GDAL>=3.0', 'rasterio>=1.1.5', 'Shapely==1.7.1',
    'rs-commons'
]

with open("README.md", "rb") as f:
    long_descr = f.read().decode("utf-8")

version = re.search(
    '^__version__\\s*=\\s*"(.*)"',
    open('vbet/__version__.py').read(),
    re.M
).group(1)

setup(name='vbet',
      version=version,
      description='Riverscapes Open Source Python VBET',
      author='Matt Reimer',
      license='MIT',
      python_requires='>3.5.2',
      long_description=long_descr,
      author_email='info@northarrowresearch.com',
      install_requires=install_requires,
      entry_points={
          "console_scripts": ['vbet = vbet.vbet:main']
      },
      zip_safe=False,
      url='https://github.com/Riverscapes/vbet',
      packages=[
          'vbet'
      ]
      )

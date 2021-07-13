#! /bin/bash
set -eu

# On OSX you must have run `brew install gdal` so that the header files are findable 
python3 --version
python3 -m venv .venv
# Make sure pip is at a good version
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip --timeout=120 install \
  Cython==0.29.23 \
  numpy>=1.21.0 \
  scipy>=1.5.1

# Need numpy before GDAL
.venv/bin/pip install GDAL==$(gdal-config --version)

# Now install everything else
.venv/bin/pip --timeout=120 install -r requirements.txt

# Install our packages as being editable
.venv/bin/pip install -e ./lib/commons
.venv/bin/pip install -e ./packages/rscontext
.venv/bin/pip install -e ./packages/vbet
.venv/bin/pip install -e ./packages/brat
.venv/bin/pip install -e ./packages/gnat
.venv/bin/pip install -e ./packages/hand
.venv/bin/pip install -e ./packages/rvd
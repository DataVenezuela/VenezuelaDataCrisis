#!/usr/bin/env bash
set -euo pipefail

python -m scrapers.cli validate --config scrapers/config/sources.demo.yaml
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml

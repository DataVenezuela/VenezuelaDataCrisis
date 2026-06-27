#!/usr/bin/env bash
set -euo pipefail

python -m scrapers.cli validate --config scrapers/config/sources.venezuela.starter.yaml
python -m scrapers.cli run --config scrapers/config/sources.venezuela.starter.yaml --limit 5

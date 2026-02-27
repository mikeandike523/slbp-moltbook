#!/bin/bash

set -euo pipefail

dn="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

"$dn/__inenv" pip install -r "$dn/../small-llm-big-projects/requirements.txt"
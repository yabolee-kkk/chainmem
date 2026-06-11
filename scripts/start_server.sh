#!/bin/bash
source /home/borong/chainmem/.venv/bin/activate
source /home/borong/.hermes/.env 2>/dev/null
export CHAINMEM_LLM_KEY="$DEEPSEEK_API_KEY"
exec python3 /home/borong/chainmem/scripts/chainmem_server.py

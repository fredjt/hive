#!/bin/bash
# T4 (0.22) probe-trio verification: 5 consecutive runs at -n 8 and
# -n 32. The trio: the two subprocess-heavy harness round-trips and
# the cut-receipt re-run publish (the load-sensitive tail).
cd "$(dirname "$0")"
TRIO="tests/test_harness_tool_loop.py::test_tool_loop_round_trip tests/test_harness_tool_loop.py::test_mcp_call_in_bash_block_routes_to_mcp tests/test_installation.py::TestCutReceipt::test_rerun_clobbers_the_receipt"
overall=0
for n in 8 32; do
  for i in 1 2 3 4 5; do
    t0=$(date +%s)
    if python -m pytest $TRIO -q --no-header -n $n > /tmp/trio-$n-$i.log 2>&1; then
      t1=$(date +%s)
      echo "TRIO -n $n run $i: PASS ($((t1-t0))s)"
    else
      t1=$(date +%s)
      echo "TRIO -n $n run $i: FAIL ($((t1-t0))s)"
      overall=1
    fi
  done
done
exit $overall

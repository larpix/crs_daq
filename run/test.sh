#!/usr/bin/env bash

python test.py &

TEST_PID=$!
echo "pid=$TEST_PID"
sed -i "s/TEST_PID=[0-9]*/TEST_PID=${TEST_PID}/" .envrc

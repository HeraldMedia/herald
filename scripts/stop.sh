#!/bin/bash
# Stop all Herald pm2 processes.
for name in herald_miner herald_validator herald_brief_board; do
  if pm2 list | grep -q "$name"; then
    pm2 stop "$name" && pm2 delete "$name"
  fi
done

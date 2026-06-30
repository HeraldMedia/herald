#!/bin/bash
# Show status of all Herald pm2 processes.
pm2 list | grep -E "herald_(miner|validator|brief_board)|App name" || echo "No Herald processes running."

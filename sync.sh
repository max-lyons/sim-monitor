#!/bin/bash
# Run on your Mac to auto-sync changes from celeste
# Usage: bash ~/Desktop/sim-monitor/sync.sh

while true; do
    rsync -avz celeste:~/code/md-learning/sim-monitor/ ~/code/sim-monitor/
    sleep 5
done

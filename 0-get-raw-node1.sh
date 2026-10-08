#!/usr/bin/env bash
set -e

mkdir -p input-raw-data
wget https://raw.githubusercontent.com/CharlesPikachu/freeproxy/refs/heads/master/proxies.json -O input-raw-data/proxies.json
#!/usr/bin/env sh
# Build the FT-E Sprint 7 C++ deterministic Formula Engine.
# Usage: sh formula_engine/build.sh
set -e
cd "$(dirname "$0")"
mkdir -p bin
g++ -std=c++17 -O2 -Wall -Wextra -o bin/formula_engine formula_engine.cpp
echo "built: $(pwd)/bin/formula_engine"

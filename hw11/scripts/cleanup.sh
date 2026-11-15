#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export DOCKER_DEFAULT_PLATFORM="${HW11_PLATFORM:-linux/arm64}"
.tools/kind delete cluster --name mlops-hw11 --kubeconfig .cache/kubeconfig
docker image rm mlops-hw11:0.1.0
# Shared base images and unrelated projects are intentionally preserved.

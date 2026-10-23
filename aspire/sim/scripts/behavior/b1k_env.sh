# shellcheck shell=bash
# Source (do not execute) to activate the BEHAVIOR-1K runtime for ASPIRE:
#
#   source scripts/behavior/b1k_env.sh            # activate only
#   source scripts/behavior/b1k_env.sh --check    # activate + print resolved paths/pins
#
# Every variable below honours a value already set in the caller's environment,
# so host-specific choices (GPU ids, weight locations) stay outside the repo.
# Nothing here reads or prints credentials.

_b1k_sim_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
_b1k_venv="${B1K_VENV:-${_b1k_sim_root}/cap/third_party/b1k/.venv}"

if [[ ! -x "${_b1k_venv}/bin/python" ]]; then
  echo "b1k_env: venv not found at ${_b1k_venv}; run scripts/behavior/setup_behavior.sh first" >&2
  return 1 2>/dev/null || exit 1
fi

# shellcheck disable=SC1091
source "${_b1k_venv}/bin/activate"

export ASPIRE_ROOT="${_b1k_sim_root}"
export PYTHON_ROOT="$(cd "${_b1k_sim_root}/../.." && pwd)"

# Isaac Sim / OmniGibson: headless, EULA accepted, GPU chosen by OMNIGIBSON_GPU_ID
# (not CUDA_VISIBLE_DEVICES). Perception servers use the default CUDA device.
export OMNI_KIT_ACCEPT_EULA=YES
export OMNIGIBSON_HEADLESS="${OMNIGIBSON_HEADLESS:-1}"
export OMNIGIBSON_GPU_ID="${OMNIGIBSON_GPU_ID:-2}"

# Isaac Sim refuses to start with these set by another Isaac install.
unset EXP_PATH CARB_APP_PATH ISAAC_PATH

# CUDA 12.x toolkit (only needed to rebuild curobo).
if [[ -z "${CUDA_HOME:-}" ]]; then
  for _c in /usr/local/cuda-12.6 /usr/local/cuda-12.4 /usr/local/cuda-12.1 /usr/local/cuda-12; do
    [[ -x "${_c}/bin/nvcc" ]] && { export CUDA_HOME="${_c}"; break; }
  done
fi

# User-level Vulkan ICD (hosts without a system nvidia_icd.json).
if [[ -z "${VK_ICD_FILENAMES:-}" && -f "${HOME}/.config/vulkan/icd.d/nvidia_icd.json" ]]; then
  export VK_ICD_FILENAMES="${HOME}/.config/vulkan/icd.d/nvidia_icd.json"
fi

# Licensed perception weights: reuse existing local copies when present.
if [[ -z "${SAM3_CHECKPOINT_PATH:-}" && -f "${HOME}/.cache/aspire/sam3/sam3.pt" ]]; then
  export SAM3_CHECKPOINT_PATH="${HOME}/.cache/aspire/sam3/sam3.pt"
fi
if [[ -z "${CONTACT_GRASPNET_CHECKPOINT_DIR:-}" ]]; then
  for _g in "${_b1k_sim_root}/cap/third_party/contact_graspnet_pytorch/checkpoints/contact_graspnet/checkpoints" \
            "${HOME}/code/ASPIRE/aspire/sim/cap/third_party/contact_graspnet_pytorch/checkpoints/contact_graspnet/checkpoints"; do
    [[ -f "${_g}/model.pt" ]] && { export CONTACT_GRASPNET_CHECKPOINT_DIR="${_g}"; break; }
  done
fi

# Isaac Sim core dumps are multi-GB; the launcher can segfault at teardown.
ulimit -c 0

if [[ "${1:-}" == "--check" ]]; then
  echo "ASPIRE_ROOT=${ASPIRE_ROOT}"
  echo "venv=${_b1k_venv}"
  echo "python=$(command -v python) ($(python -V 2>&1))"
  echo "OMNIGIBSON_GPU_ID=${OMNIGIBSON_GPU_ID} OMNIGIBSON_HEADLESS=${OMNIGIBSON_HEADLESS}"
  echo "CUDA_HOME=${CUDA_HOME:-<unset>}"
  echo "VK_ICD_FILENAMES=${VK_ICD_FILENAMES:-<unset>}"
  echo "SAM3_CHECKPOINT_PATH=${SAM3_CHECKPOINT_PATH:-<unset>}"
  echo "CONTACT_GRASPNET_CHECKPOINT_DIR=${CONTACT_GRASPNET_CHECKPOINT_DIR:-<unset>}"
  python - <<'PY'
import importlib.metadata as md
for name in ("torch", "torchvision", "numpy", "setuptools", "omnigibson", "isaacsim",
             "nvidia_curobo", "sam3", "contact_graspnet_pytorch", "opencv-python-headless", "jax", "pyroki"):
    try:
        print(f"  {name}=={md.version(name)}")
    except md.PackageNotFoundError:
        print(f"  {name}: not installed")
PY
fi

unset _b1k_sim_root _b1k_venv _c _g

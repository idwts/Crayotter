#!/bin/bash
# transformer_engine 完整安装脚本（解决所有依赖问题）

set -e

echo "=== Transformer Engine 完整安装脚本 ==="


# 步骤 1: 确保基础依赖已安装
echo ""
echo "=== 步骤 1: 检查并安装基础依赖 ==="
uv pip install --upgrade pip setuptools wheel ninja 2>&1 | tail -10
# 安装完整的 CUDA 工具包（包含头文件和编译器）
# conda install -c nvidia cuda-toolkit=12.4

# 重要!!!!!!!!!!!!
# conda install -c nvidia cuda-nvtx -y
# conda install -c nvidia cuda-nvtx-dev -y

# 步骤 2: 安装 cuDNN（如果还没有）
echo ""
echo "=== 步骤 2: 确保 cuDNN 已安装 ==="
if [ ! -f "$CONDA_PREFIX/include/cudnn.h" ]; then
    echo "安装 cuDNN via conda..."
    conda install -c nvidia cudnn -y 2>&1 | tail -10
fi

# 步骤 3: 安装 NCCL（之前遇到过这个问题）
echo ""
echo "=== 步骤 3: 检查并安装 NCCL ==="
if [ ! -f "$CONDA_PREFIX/include/nccl.h" ] && [ ! -f "/usr/include/nccl.h" ]; then
    echo "尝试安装 NCCL via conda..."
    conda install -c nvidia nccl -y 2>&1 | tail -10 || {
        echo "⚠️  conda 安装 NCCL 失败，尝试系统级安装..."
        echo "lsz" | sudo -S apt-get install libnccl-dev -y 2>&1 | tail -10 || echo "⚠️  系统级安装也失败"
    }
fi

# 步骤 4: 设置所有必要的环境变量
echo ""
echo "=== 步骤 4: 设置编译环境变量 ==="
export CUDA_HOME=$CONDA_PREFIX
export CUDNN_INCLUDE_DIR=$CONDA_PREFIX/include
export CUDNN_LIBRARY_DIR=$CONDA_PREFIX/lib
export LD_LIBRARY_PATH=$CUDNN_LIBRARY_DIR:$LD_LIBRARY_PATH

# 设置 NCCL 路径（如果存在）
if [ -f "$CONDA_PREFIX/include/nccl.h" ]; then
    export NCCL_INCLUDE_DIR=$CONDA_PREFIX/include
    export NCCL_LIB_DIR=$CONDA_PREFIX/lib
elif [ -f "/usr/include/nccl.h" ]; then
    export NCCL_INCLUDE_DIR=/usr/include
    export NCCL_LIB_DIR=/usr/lib/x86_64-linux-gnu
    export LD_LIBRARY_PATH=$NCCL_LIB_DIR:$LD_LIBRARY_PATH
fi

# 设置 CUDA 路径
if [ -d "/usr/local/cuda" ]; then
    export CUDA_HOME=/usr/local/cuda
    export PATH=$CUDA_HOME/bin:$PATH
    export LD_LIBRARY_PATH=$CUDA_HOME/lib64:$LD_LIBRARY_PATH
fi

echo "环境变量设置完成："
echo "  CUDA_HOME=$CUDA_HOME"
echo "  CUDNN_INCLUDE_DIR=$CUDNN_INCLUDE_DIR"
echo "  NCCL_INCLUDE_DIR=${NCCL_INCLUDE_DIR:-未设置}"

# 步骤 5: 尝试安装 transformer_engine（使用 --no-build-isolation 避免依赖问题）
echo ""
echo "=== 步骤 5: 安装 transformer_engine ==="
echo "方法 1: 使用 --no-build-isolation（推荐）..."

# 先尝试从标准 PyPI 安装
uv pip install transformer_engine[pytorch] --no-build-isolation 2>&1 | tee /tmp/te_install.log && {
    echo "✓ transformer_engine 安装成功！"
    exit 0
} || {
    echo "⚠️  标准 PyPI 安装失败，查看详细错误..."
    tail -50 /tmp/te_install.log | grep -A 20 "error\|Error\|ERROR\|fatal" || tail -30 /tmp/te_install.log
}

# 如果失败，尝试从 NVIDIA PyPI 安装
echo ""
echo "方法 2: 从 NVIDIA PyPI 安装..."
uv pip install -i https://pypi.nvidia.com transformer_engine[pytorch] --no-build-isolation 2>&1 | tee /tmp/te_install_nvidia.log && {
    echo "✓ 从 NVIDIA PyPI 安装成功！"
    exit 0
} || {
    echo "⚠️  NVIDIA PyPI 安装也失败，查看详细错误..."
    tail -50 /tmp/te_install_nvidia.log | grep -A 20 "error\|Error\|ERROR\|fatal" || tail -30 /tmp/te_install_nvidia.log
}

# 如果还是失败，尝试安装特定版本
echo ""
echo "方法 3: 尝试安装较旧版本（可能更容易编译）..."
proxychains4 pip install "transformer_engine[pytorch]==2.4.0" --no-build-isolation 2>&1 | tail -30 && {
    echo "✓ 安装旧版本成功！"
    exit 0
} || echo "⚠️  旧版本安装也失败"

# 最终诊断
echo ""
echo "=== 安装失败诊断 ==="
echo "检查关键文件："
echo "  cuDNN header: $([ -f "$CUDNN_INCLUDE_DIR/cudnn.h" ] && echo "✓ 存在" || echo "✗ 不存在")"
echo "  NCCL header: $([ -f "${NCCL_INCLUDE_DIR:-/none}/nccl.h" ] && echo "✓ 存在" || echo "✗ 不存在")"
echo "  CUDA: $([ -d "$CUDA_HOME" ] && echo "✓ 存在" || echo "✗ 不存在")"

echo ""
echo "如果仍然失败，可能需要："
echo "1. 安装完整的 CUDA 工具包: conda install -c nvidia cuda-toolkit"
echo "2. 或者安装系统级开发包: sudo apt-get install libcudnn8-dev libnccl-dev"
echo "3. 检查编译错误日志: cat /tmp/te_install.log"


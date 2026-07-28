"""推論環境セットアップのコマンド組み立ての検証(uv は実行しない)"""
from pathlib import Path

from mosaic_tool.detect import runtime

UV = Path("/app/uv")
RUNTIME = Path("/app/runtime")


def test_venv_command_pins_python_version():
    cmd = runtime.venv_command(UV, RUNTIME)
    assert cmd[:3] == [str(UV), "venv", str(RUNTIME)]
    assert cmd[-2:] == ["--python", runtime.PYTHON_VERSION]


def test_venv_command_recreates_existing_venv():
    """既存の runtime/ があっても再セットアップできること"""
    assert "--clear" in runtime.venv_command(UV, RUNTIME)


def test_install_command_targets_the_venv():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=False)
    assert cmd[:3] == [str(UV), "pip", "install"]
    assert "--python" in cmd and str(RUNTIME) in cmd
    for package in runtime.PACKAGES:
        assert package in cmd


def test_cpu_install_has_no_cuda_index():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=False)
    assert "--extra-index-url" not in cmd


def test_gpu_install_adds_cuda_index():
    cmd = runtime.install_command(UV, RUNTIME, use_gpu=True)
    assert cmd[-2:] == ["--extra-index-url", runtime.TORCH_CUDA_INDEX_URL]


def test_has_nvidia_gpu_uses_nvidia_smi(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "C:/w/nvidia-smi.exe")
    assert runtime.has_nvidia_gpu()
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    assert not runtime.has_nvidia_gpu()
